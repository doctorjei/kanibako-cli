"""kanibako code: open host VS Code attached to a running box.

Purely a launcher: it resolves a box, verifies the box is running, builds a
VS Code "attach to running container" URI pointing at the box's in-box
workspace, and launches the host ``code`` CLI.  It changes NO launch/box
behavior.
"""

from __future__ import annotations

import argparse
import binascii
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from kanibako.box_lifecycle import is_vscode_server_path_part
from kanibako.settings.config import config_file_path, load_config
from kanibako.runtime.container import ContainerRuntime
from kanibako.errors import ContainerError, KanibakoError
from kanibako.log import get_logger
from kanibako.settings.paths import (
    xdg,
    load_std_paths,
    resolve_box_target,
)
from kanibako.settings.settings_resolve import GUEST_HOME
from kanibako.utils import container_name_for
from kanibako.vscode.vscode_config import (
    attached_container_config_path,
    load_jsonc,
    seed_attached_container_config,
)


def add_code_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "code",
        help="Open host VS Code attached to a running box",
        description=(
            "Open your host VS Code attached to a running kanibako box "
            "(Dev Containers: Attach to Running Container), opened at the "
            "box's workspace."
        ),
    )
    p.add_argument(
        "project", nargs="?", default=None,
        help="Project name or path (default: cwd)",
    )
    p.add_argument(
        "--remote", metavar="HOST", default=None,
        help=(
            "Attach LOCAL VS Code to a box on a REMOTE host over SSH "
            "(HOST resolves via your ~/.ssh/config). Requires a box name."
        ),
    )
    p.set_defaults(func=run_code)


def _attach_uri(container_name: str, context: str | None = None) -> str:
    """Build the VS Code ``vscode-remote://`` attach URI for *container_name*.

    The container is named by a hex-encoded JSON object
    (``{"containerName":"<name>"}``), followed immediately by the in-box
    workspace path.  When *context* is given, a per-window routing token is
    embedded as ``settings.context`` (the R1 context-token dispatch channel
    for ``--remote``).

    The name is BARE — never the docker-API-convention ``/<name>``: podman
    rejects the leading slash on both local CLI and remote API lookups
    (``Error: no such container /<name>`` — confirmed on 4.9.3 local, 5.4.2
    local AND 5.4.2 remote, plus a live local-attach failure on Raiju), while
    the Dev Containers extension accepts a bare name or ID.  The slash form
    shipped in rc4-rc7 local URIs but was never exercised end-to-end against
    podman until 2026-07-09 (the picker flow, which the earlier validations
    used, never goes through this payload).
    """
    payload_obj: dict[str, object] = {"containerName": container_name}
    if context is not None:
        payload_obj["settings"] = {"context": context}
    payload = json.dumps(payload_obj, separators=(",", ":"))
    hex_name = binascii.hexlify(payload.encode()).decode()
    workspace_path = GUEST_HOME + "/workspace"
    return f"vscode-remote://attached-container+{hex_name}{workspace_path}"


class _CodeShimError(Exception):
    """The resolved ``code`` CLI is VS Code's in-container remote-cli shim.

    Raised by :func:`_resolve_code_cli` when ``kanibako code`` is (almost
    certainly) running INSIDE a container that a VS Code client is attached to,
    where the ``code`` on PATH is the remote shim that dispatches to the
    attaching desktop client rather than launching a local editor.
    """


_CODE_SHIM_MSG = (
    "Error: the 'code' found on your PATH is VS Code's in-container remote "
    "shim.\n"
    "  You appear to be running 'kanibako code' INSIDE a container that a VS "
    "Code client is attached to; this 'code' would open windows on the "
    "ATTACHING desktop, not here.\n"
    "  Run 'kanibako code' from the host instead."
)


def _resolve_code_cli() -> str | None:
    """Resolve the host ``code`` CLI path, refusing VS Code's remote-cli shim.

    Returns the ``shutil.which("code")`` path (the ORIGINAL PATH entry, so the
    launch is byte-for-byte what the user's PATH selects), or ``None`` when no
    ``code`` is on PATH (callers print their own "missing" guidance).

    Raises :class:`_CodeShimError` when the resolved binary is VS Code's
    in-container ``remote-cli`` shim.  Detection follows symlinks
    (``Path.resolve()``) and fires on EITHER prong:

    (a) the resolved path lands inside a remote-server tree —
        ``.vscode-server`` plus the known variants ``.vscode-server-insiders``,
        ``.vscode-server-oss``, and ``.cursor-server`` — catching those shims
        regardless of environment (a stale tmux shell predating the attach has
        no ``VSCODE_IPC_HOOK_CLI`` yet still resolves the shim);
    (b) ``VSCODE_IPC_HOOK_CLI`` is set (an attached-terminal shell) AND the
        resolved path lives in a ``remote-cli`` dir — a belt for server-dir
        layouts (a) doesn't know about.  A variant client using a different
        IPC env var AND an unknown server dir slips both prongs; known gap,
        acceptable for the mainline.

    The env var ALONE never refuses (a box may legitimately carry a real
    ``code`` on PATH while the var leaks in), and its ABSENCE never skips the
    path check (prong (a) runs unconditionally).  A real host install
    (``/usr/bin/code``, ``~/.local/share/code/bin/code``) matches neither prong.
    """
    code_bin = shutil.which("code")
    if code_bin is None:
        return None
    try:
        resolved_parts = Path(code_bin).resolve().parts
    except OSError:  # symlink loop etc.: not identifiable as a shim; let the
        return code_bin  # actual launch surface the real error
    ipc_set = bool(os.environ.get("VSCODE_IPC_HOOK_CLI"))
    is_server_tree = any(
        is_vscode_server_path_part(part) for part in resolved_parts
    )
    if is_server_tree or (ipc_set and "remote-cli" in resolved_parts):
        raise _CodeShimError(_CODE_SHIM_MSG)
    return code_bin


def run_code(args: argparse.Namespace) -> int:
    dest = getattr(args, "remote", None)
    if dest:
        return _run_code_remote(args, dest)

    from kanibako.commands.flags import resolve_subject_value
    project_dir = resolve_subject_value(
        getattr(args, "project", None), getattr(args, "box", None),
    )

    try:
        runtime = ContainerRuntime()
    except ContainerError:
        print(
            "Error: No container runtime found.\n"
            "Install podman (https://podman.io/) or Docker.",
            file=sys.stderr,
        )
        return 1

    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    # EXPLICIT-CREATE gate (Jei 2026-07-11g): `code` never auto-CREATES a box —
    # only auto-STARTS an existing one.  Resolve NON-materialisingly (``initialize=
    # False`` → no mkdir/seed; ``register=True`` so a half-created, not-yet-
    # registered box does NOT resurrect off the create journal) and, when no
    # EXISTING registered box resolves, surface the SAME "no box; run create" error
    # the launch path uses — one clean message instead of the generic auto-start
    # failure.  Registration is the existence signal (non-empty ``proj.name``); a
    # bare token naming nothing makes the resolver refuse (ProjectError).  (The
    # start_detached leg below re-gates identically at ``_run_container`` for
    # defense in depth.)
    from kanibako.commands.start import _no_box_error
    from kanibako.errors import ProjectError
    try:
        proj = resolve_box_target(
            std, config, project_dir,
            initialize=False, register=True, warn=False,
        )
    except ProjectError:
        proj = None
    if proj is None or not proj.name:
        print(_no_box_error(project_dir, std), file=sys.stderr)
        return 1
    cname = container_name_for(proj)

    # Fail fast if the host `code` CLI is missing or is the in-container remote
    # shim — BEFORE auto-starting a box, so a missing/wrong prerequisite never
    # leaves a background box behind.
    try:
        code_bin = _resolve_code_cli()
    except _CodeShimError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if code_bin is None:
        print(
            "Error: the VS Code 'code' CLI was not found on your PATH.\n"
            "  Install VS Code and add its 'code' command to PATH "
            "(Command Palette: 'Shell Command: Install code command in PATH').\n"
            "  You also need the Dev Containers extension, with "
            "'dev.containers.dockerPath' set to 'podman'.",
            file=sys.stderr,
        )
        return 1

    if not runtime.is_running(cname):
        # AUTO-START (Phase 4): the box isn't up, so start it DETACHED with a bare
        # keep-alive PID-1 (a tmux session running a shell, NOT the agent) and no
        # terminal attach — the box stays Up while the user works through VS Code.
        # Detach-by-default is safe for `code`: the VS Code panel self-serves auth
        # host-side, decoupled from the in-box CLI credentials.
        name = proj.name or cname
        print(
            f"Box '{name}' is not running; starting it in the background...",
            file=sys.stderr,
        )
        from kanibako.commands.start import start_detached
        rc = start_detached(project_dir)
        if rc != 0:
            print(
                f"Error: could not auto-start box '{name}' for VS Code.",
                file=sys.stderr,
            )
            return rc
        # Re-resolve the (existing) box after the auto-START so proj/cname reflect
        # its now-running state before seeding + attaching.  (The box already
        # existed — the explicit-create gate above guaranteed it — so this is a
        # refresh, not a materialisation.)
        proj = resolve_box_target(std, config, project_dir, initialize=False)
        cname = container_name_for(proj)
        if not runtime.is_running(cname):
            print(
                f"Error: box '{name}' did not come up after auto-start.",
                file=sys.stderr,
            )
            return 1

    # Best-effort: seed the attached-container config so VS Code opens the box's
    # workspace and auto-installs the box agent's editor extension on attach.
    # NEVER blocks the launch — a failure here (unresolved agent, unwritable path)
    # is logged and swallowed so `code` still opens (Phase-1 zero-launch-delta).
    _seed_attached_config(runtime, std, proj, cname)

    uri = _attach_uri(cname)
    name = proj.name or cname
    print(f"Opening VS Code attached to box '{name}'...")
    subprocess.run([code_bin, "--folder-uri", uri])
    return 0


def _extension_for_agent(agent_name: str, project_path) -> str | None:
    """Resolve *agent_name*'s ``descriptor.vscode_extension`` (or ``None``).

    ``agent_name`` is a NODE-name; the plugin/target is keyed by its HARNESS
    (``harness_of``), exactly as ``stop.py`` / ``start.py`` resolve a stamped box.
    A descriptor-less target (the no-agent shell) or an unset extension → ``None``.

    *project_path* seeds any project-scoped plugin lookup; ``None`` (the
    ``--remote`` seed, which has no LOCAL project) skips the project-dependent
    fallbacks and resolves the plugin from the global/editable finders only.
    """
    from kanibako.agent_ref import harness_of
    from kanibako.targets import resolve_target

    target = resolve_target(harness_of(agent_name), project_path)
    desc = target.descriptor
    return desc.vscode_extension if desc is not None else None


def _resolve_box_agent_node(runtime, std, proj, container_name: str) -> str | None:
    """Best-effort: the RUNNING box's authoritative agent NODE-name (or ``None``).

    STAMP-FIRST, mirroring ``stop.py._writeback_on_stop`` and ``start.py``'s
    reattach fast-source: a running box's authoritative agent is its
    ``KANIBAKO_AGENT`` launch stamp (``runtime.inspect_env``), NOT the create-time
    cascade.  Using the stamp avoids two cascade mis-resolutions on a running box:
    (1) 2+ installed agents + no system default → the cascade RAISES (seed nothing
    for a live claude box); (2) a system default that has since diverged from the
    box's actually-running agent → seed the WRONG agent.

    Falls back to the ``select_agent`` create-cascade ONLY for pre-stamp (older)
    boxes with no ``KANIBAKO_AGENT`` env.  Swallows every failure → ``None``.
    NEVER raises.  Resolved ONCE per ``code`` invocation and consumed by the
    extension seed (:func:`_resolve_box_vscode_extension`) so the box is inspected
    a single time.
    """
    try:
        stamp = runtime.inspect_env(container_name, "KANIBAKO_AGENT")
        if stamp:
            return stamp

        # Pre-stamp (older) box: fall back to the create-time selection cascade
        # (agent_select reads the SAME box-tier file ``box set
        # pref.system.agent=…`` writes — the ONE tier pair, M-8).
        from kanibako.settings.agent_select import select_agent

        return select_agent(std=std, proj=proj).node or None
    except Exception:
        get_logger("code").debug(
            "could not resolve box agent name; seeding none",
            exc_info=True,
        )
        return None


def _resolve_box_vscode_extension(agent_name: str | None, proj) -> str | None:
    """Best-effort: *agent_name*'s ``descriptor.vscode_extension`` (or ``None``).

    Takes the pre-resolved box agent NODE-name (see :func:`_resolve_box_agent_node`)
    and maps it to its editor extension.  Swallows every failure (descriptor-less
    / no-agent shell, unset extension) → ``None``.  NEVER raises.
    """
    if agent_name is None:
        return None
    try:
        return _extension_for_agent(agent_name, proj.project_path)
    except Exception:
        get_logger("code").debug(
            "could not resolve box agent VS Code extension; seeding none",
            exc_info=True,
        )
        return None


def _resolve_box_image(runtime, proj, container_name: str) -> str | None:
    """Best-effort: the image reference keying the box's attached-container config.

    The attached config is IMAGE-shared, so we must key it by the box's image.
    STAMP-FIRST-style, mirroring ``_resolve_box_vscode_extension``: prefer the
    RUNNING container's ACTUAL image (``runtime.container_image``) — the
    authoritative source for a live box.  Falls back to the box's configured
    ``box_image`` (the create-time merged config, which itself defaults to the
    packaged ``ghcr.io/doctorjei/kanibako-oci:latest``).  Returns ``None`` only
    if every source fails — callers then SKIP seeding rather than crash.

    ⚑ A ``KanibakoError`` from that fallback resolve is WARNED, not swallowed: the
    skip degrades the attach visibly (no workspace folder, no extension), so its
    cause must not be debug-only.  The return is still ``None``.
    """
    image = runtime.container_image(container_name)
    if image:
        return image
    try:
        from kanibako.settings.config import load_merged_config
        from kanibako.settings.paths import box_workset_settings_paths

        # The ONE tier pair (M-8): the configured image comes from the same box-tier
        # file ``box set box.image=…`` writes.
        _box_path, _ws_path = box_workset_settings_paths(proj)
        merged = load_merged_config(
            config_file_path(xdg("XDG_CONFIG_HOME", ".config")),
            _box_path, workset_path=_ws_path,
        )
        return merged.box_image or None
    except KanibakoError as exc:
        # Still ``None`` — the seed stays best-effort and the launch keeps its
        # zero-delta — but NOT silent.  A ``KanibakoError`` is a message already
        # written to be shown to a user (``errors.py``: the hierarchy cli.py
        # catches), above all the closed-keyspace refusal, which names the
        # offending entry and every file the resolve loaded.  At debug level the
        # user saw NOTHING and simply got a VS Code window with no workspace
        # folder and no agent extension — a real symptom with its cause hidden.
        #
        # WARNING, not an abort: the condition needs the user's hand on a settings
        # file, but the attach itself still works.  Anything else keeps the debug
        # line, which is right for the unforeseen.
        get_logger("code").warning(
            "VS Code will attach without the box's workspace folder or agent "
            "extension: the box image could not be resolved.\n%s",
            exc,
        )
        return None
    except Exception:
        get_logger("code").debug(
            "could not resolve box image; skipping attached-config seed",
            exc_info=True,
        )
        return None


def _write_attached_config(path, extension: str | None) -> None:
    """Write the box's attached-container config; WARNS (never aborts) on an OS failure.

    ⚑ The ``OSError`` here is the ONE failure in the whole seed that a user both
    CAUSES and can FIX: an unwritable or full VS Code config home, or an
    ``imageConfigs`` path component that is not a directory.  It arrives from
    ``seed_attached_container_config``'s ``mkdir``/``write_text`` carrying the
    errno AND the offending path, so the message needs nothing but the exception
    itself.  Under the callers' blanket debug swallow the user saw NOTHING and
    got a VS Code window with no workspace folder and no agent extension — the
    symptom with its cause hidden and a fix they could have acted on withheld.

    WARNING, not an abort: the launch keeps its zero-delta.  ``code``'s job is to
    open the editor; the seed only enriches the attach, so a failed seed must
    cost the user a warning, never their editor.

    ⚑ ONE writer for BOTH legs (:func:`_seed_attached_config` and
    :func:`_seed_remote_attached_config`), and the message is leg-agnostic
    because the FAILURE is: ``--remote`` seeds the LOCAL config home too (keyed
    by the remote box's image), so the unwritable directory and the user's remedy
    are the same on either path.
    """
    try:
        seed_attached_container_config(
            path,
            workspace_folder=GUEST_HOME + "/workspace",
            extension=extension,
        )
    except OSError as exc:
        # ⚑ "may" is exact, not a hedge: a PREVIOUS successful seed may still be
        # on disk, in which case the attach degrades only by whatever this run
        # would have added.  A first-ever seed that fails degrades it fully.
        get_logger("code").warning(
            "VS Code's attached-container config for this box could not be "
            "written, so the attach may open without the box's workspace "
            "folder or its agent extension.\n%s",
            exc,
        )


def _seed_attached_config(runtime, std, proj, container_name: str) -> None:
    """Best-effort seed of the box's attached-container config. NEVER raises.

    UNION-MERGES the box workspace + the box agent's editor extension into the
    IMAGE-keyed devcontainer.json-subset VS Code reads on attach (preserving
    everything VS Code/the user already wrote).  The launch is unaffected by any
    failure here (Phase-1 zero-launch-delta).

    ⚑ Each STEP owns its own reporting, and this blanket catch is deliberately
    NOT where a foreseen failure is handled:

    * image resolution — :func:`_resolve_box_image` (WARNS a settings refusal,
      debug otherwise) and returns ``None``, which SKIPS the seed;
    * agent + extension resolution — :func:`_resolve_box_agent_node` /
      :func:`_resolve_box_vscode_extension`, both debug-and-``None``;
    * the WRITE — :func:`_write_attached_config`, which WARNS on ``OSError``.

    What is left for this catch is therefore the UNFORESEEN: a bug in the seed
    itself, or an ``OSError`` from the runtime probe inside ``_resolve_box_image``
    (podman gone between the ``is_running`` check and here).  Neither names a
    condition the user can act on, so both stay at debug — and the catch stays
    blanket so a seed bug can never cost the user their editor.
    """
    try:
        image_ref = _resolve_box_image(runtime, proj, container_name)
        if image_ref is None:
            return  # can't key the image-shared config → skip, never crash
        # Resolve the box agent ONCE (STAMP-first) for the extension seed.
        agent_name = _resolve_box_agent_node(runtime, std, proj, container_name)
        extension = _resolve_box_vscode_extension(agent_name, proj)
        path = attached_container_config_path(
            image_ref, xdg("XDG_CONFIG_HOME", ".config"),
        )
        _write_attached_config(path, extension)
    except Exception:
        get_logger("code").debug(
            "failed to seed VS Code attached-container config", exc_info=True,
        )


# ---------------------------------------------------------------------------
# FF-1: `kanibako code --remote <host>` — LOCAL VS Code → REMOTE podman (A').
# ---------------------------------------------------------------------------

_MISSING_CODE_MSG = (
    "Error: the VS Code 'code' CLI was not found on your PATH.\n"
    "  Install VS Code and add its 'code' command to PATH "
    "(Command Palette: 'Shell Command: Install code command in PATH').\n"
    "  You also need the Dev Containers extension."
)


def _wire_docker_path(wrapper_path) -> int | None:
    """Ensure ``dev.containers.dockerPath`` points at the kanibako wrapper.

    Returns ``None`` to PROCEED (already wired, or updated on the user's OK),
    or a non-zero exit code to abort.  The already-wired CHECK reads the file
    JSONC-tolerantly (``load_jsonc``, same reader as diagnose); a WRITE is only
    auto-applied to strict-JSON files (merge-preserving read/modify/write, same
    pattern as ``seed_claude_bypass_permissions``) — a JSONC file needing a
    change, an unreadable file, or a non-tty session prints the exact manual
    snippet and aborts.  NEVER clobbers a file it cannot losslessly rewrite.
    """
    wrapper_str = str(wrapper_path)
    settings_path = (
        xdg("XDG_CONFIG_HOME", ".config") / "Code" / "User" / "settings.json"
    )
    snippet = (
        f"  Add this to your VS Code user settings.json ({settings_path}):\n"
        f'      "dev.containers.dockerPath": "{wrapper_str}"'
    )

    existing_text: str | None = None
    if settings_path.is_file():
        try:
            existing_text = settings_path.read_text(encoding="utf-8")
        except OSError:
            print(
                f"Error: cannot read {settings_path}.\n{snippet}",
                file=sys.stderr,
            )
            return 1

    if existing_text is not None and existing_text.strip():
        # JSONC-tolerant read for the already-wired CHECK (same reader as
        # diagnose) — a commented settings.json that already points at the
        # wrapper must proceed, not dead-end here.
        data = load_jsonc(existing_text)
        if not isinstance(data, dict):
            print(
                "Error: your VS Code settings.json could not be read as a "
                "JSON(C) object; refusing to modify it.\n" + snippet,
                file=sys.stderr,
            )
            return 1
    else:
        data = {}

    if data.get("dev.containers.dockerPath") == wrapper_str:
        return None  # already wired

    # A WRITE is needed.  Rewriting via json.dumps drops JSONC comments, so
    # only auto-modify files that are already strict JSON; otherwise hand the
    # user the exact snippet (NEVER clobber).
    if existing_text is not None and existing_text.strip():
        try:
            json.loads(existing_text)
        except ValueError:
            print(
                "Error: your VS Code settings.json contains JSONC comments or "
                "trailing commas; refusing to rewrite it (comments would be "
                "lost).\n" + snippet,
                file=sys.stderr,
            )
            return 1

    if not sys.stdin.isatty():
        print(
            "VS Code 'dev.containers.dockerPath' must point at the kanibako "
            "dispatch wrapper for --remote.\n" + snippet,
            file=sys.stderr,
        )
        return 1

    print(
        f"Update VS Code 'dev.containers.dockerPath' -> {wrapper_str}? [y/N] ",
        end="", flush=True,
    )
    try:
        resp = input()
    except (EOFError, KeyboardInterrupt):
        print()
        print(snippet, file=sys.stderr)
        return 1
    if resp.strip().lower() not in ("y", "yes"):
        print(snippet, file=sys.stderr)
        return 1

    data["dev.containers.dockerPath"] = wrapper_str
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Updated {settings_path}.", file=sys.stderr)
    return None


def _seed_remote_attached_config(engine, container_name: str) -> None:
    """Best-effort seed of the LOCAL attached-container config keyed by the
    REMOTE box's image.  NEVER raises (zero-launch-delta).

    STAMP-ONLY agent resolution (``KANIBAKO_AGENT`` via the RemoteEngine): there
    is no LOCAL box to run the merged-config cascade against, so a pre-stamp
    remote box simply seeds no extension (the workspace folder still seeds).

    ⚑ Like the local leg, each STEP owns its reporting and the blanket catch is
    NOT where a foreseen failure is handled:

    * image resolution — ``RemoteEngine.container_image`` returns ``None`` on a
      failed probe, which SKIPS the seed;
    * agent + extension resolution — the inner ``except`` below, extension
      ``None``;
    * the WRITE — :func:`_write_attached_config`, which WARNS on ``OSError``.

    What is left for this catch is the UNFORESEEN: a bug in the seed, or an
    ``OSError`` from the podman probe inside ``container_image`` (the tunnel or
    the binary gone since ``preflight_engine``).  Neither names a condition the
    user can act on, so both stay at debug — and the catch stays blanket so a
    seed bug can never cost the user their editor.
    """
    try:
        image_ref = engine.container_image(container_name)
        if image_ref is None:
            return
        extension = None
        try:
            stamp = engine.inspect_env(container_name, "KANIBAKO_AGENT")
            if stamp:
                # No LOCAL project → resolve the plugin with project_path=None.
                extension = _extension_for_agent(stamp, None)
        except Exception:
            extension = None
        path = attached_container_config_path(
            image_ref, xdg("XDG_CONFIG_HOME", ".config"),
        )
        _write_attached_config(path, extension)
    except Exception:
        get_logger("code").debug(
            "failed to seed remote VS Code attached-container config",
            exc_info=True,
        )


def _run_code_remote(args: argparse.Namespace, dest: str) -> int:
    """`kanibako code --remote <host> <box>`: attach LOCAL VS Code to a REMOTE box.

    A' topology: local VS Code drives the remote rootless podman socket over an
    ssh mux; kanibako lifecycle runs on the remote host over plain ssh.  See
    :mod:`kanibako.vscode.vscode_remote`.
    """
    from kanibako.vscode import vscode_remote as vr

    # --remote REQUIRES an explicit box (no remote cwd resolution): accept the
    # positional or the blanket --box flag; error if neither is given.
    box = getattr(args, "project", None) or getattr(args, "box", None)
    if not box:
        print(
            "Error: 'kanibako code --remote <host> <box>' requires a box "
            "name (there is no remote cwd to resolve).",
            file=sys.stderr,
        )
        return 1

    # (a) Fail fast on the LOCAL prerequisites: the `code` CLI, and podman as
    # the --remote client (podman drives the remote socket; docker is not it).
    # This runs BEFORE any ssh/probe work so an in-container remote shim is
    # refused up front rather than after establishing a tunnel.
    try:
        code_bin = _resolve_code_cli()
    except _CodeShimError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if code_bin is None:
        print(_MISSING_CODE_MSG, file=sys.stderr)
        return 1
    if shutil.which("podman") is None:
        print(
            "Error: 'podman' was not found on your PATH.\n"
            "  --remote uses local podman as the client for the remote "
            "engine; install podman (https://podman.io/).",
            file=sys.stderr,
        )
        return 1

    # (b) dev.containers.dockerPath must point at the kanibako dispatch wrapper.
    wrapper_path = vr.dispatch_wrapper_path()
    rc = _wire_docker_path(wrapper_path)
    if rc is not None:
        return rc

    # (c) Install/refresh the wrapper, probe the remote, then write the docker
    # context meta + connection store entry.
    vr.ensure_dispatch_wrapper()
    try:
        uid = vr.probe_remote(dest)
    except KanibakoError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # Engine dials the LOCAL end of a kanibako-owned ssh tunnel: podman talks to
    # `unix://<local.sock>` (never its golang ssh: client), so the REAL ssh
    # binary makes the tunnel and reads ~/.ssh/config (alias/port/ProxyJump/
    # key-files all work, no ssh-agent requirement).
    context = vr.remote_context_name(dest)
    local_sock = vr.tunnel_socket_path(context)
    url = vr.engine_url(local_sock)
    remote_sock = vr.remote_socket_path(uid)
    try:
        vr.ensure_docker_context_meta(context, url)
    except Exception:
        get_logger("code").debug(
            "could not write docker context meta", exc_info=True,
        )
    # Store the unix:// url + the pieces the wrapper needs to re-establish the
    # tunnel (SOCK/REMOTE_SOCK/DEST); keep the ORIGINAL opaque dest.
    vr.write_context_entry(
        context, url=url, dest=dest, uid=uid,
        sock=str(local_sock), remote_sock=remote_sock,
    )

    # Bring up the ssh tunnel now (idempotent) so the engine dials a live local
    # socket; the wrapper re-establishes it later on demand.
    try:
        vr.ensure_tunnel(dest, uid, local_sock)
    except KanibakoError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # Pre-flight the ENGINE leg before the lifecycle legs run: a dead tunnel
    # surfaces podman's own stderr + a tunnel remediation here, instead of a
    # downstream, unexplained "did not come up".
    engine = vr.RemoteEngine(url)
    try:
        vr.preflight_engine(engine)
    except KanibakoError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # (d) Lifecycle: start the remote box DETACHED + WARM-ONLY and read back its
    # cname.  --warm-only fronts the remote box with the panel-watch supervisor and
    # NO CLI agent, so the local VS Code panel is the SOLE agent — matching the local
    # `code` warm-up and closing the two-agent ~/.claude split-brain on the remote
    # leg.  Hard-required (no silent fall back to a plain `start --detach`
    # supervised-agent box: a split-brain is worse than a clear upgrade error).
    result = vr.remote_run_kanibako(
        dest, ["start", "--detach", "--warm-only", "--print-container", str(box)],
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        low = stderr.lower()
        hint = ""
        if "command not found" in low or "kanibako: not found" in low:
            hint = (
                "\n  Hint: kanibako was not found on the remote host "
                "(its ~/.local/bin is already put on PATH). Is kanibako "
                "installed for your user there (pipx/uv/pip --user)?"
            )
        elif "warm-only" in low:
            hint = (
                "\n  Hint: the remote kanibako is too old for --warm-only "
                "(agent-independent `code`); upgrade it so the VS Code panel is "
                "the sole agent (no two-agent split-brain on the remote box)."
            )
        elif "print-container" in low or "unrecognized arguments" in low:
            hint = (
                "\n  Hint: the remote kanibako is too old for "
                "--print-container; upgrade it (needs >= 1.7.0)."
            )
        elif "no box at" in low:
            # Explicit-create (Jei 2026-07-11g): the REMOTE box does not exist and
            # a launch never auto-creates one.  `create` must be run ON THE REMOTE
            # host — make that unambiguous (the bare "run 'kanibako create'" in the
            # remote stderr reads as a local suggestion otherwise).
            hint = (
                f"\n  Hint: box '{box}' does not exist on the remote host "
                f"'{dest}'.  Create it THERE first, e.g.: "
                f"ssh {dest} kanibako create {box}"
            )
        message = vr.format_remote_failure(
            "kanibako start --detach --warm-only", dest, stderr,
        )
        print(f"Error: {message}{hint}", file=sys.stderr)
        return 1
    lines = [ln for ln in (result.stdout or "").splitlines() if ln.strip()]
    if not lines:
        print(
            f"Error: remote start on '{dest}' printed no container name.",
            file=sys.stderr,
        )
        return 1
    cname = lines[-1].strip()

    # (e) Verify the remote box is actually running via the remote engine
    # (reusing the pre-flighted engine).  On failure, surface podman's own
    # inspect stderr — it names the underlying problem a bare bool would hide.
    running, inspect_err = engine.running_with_stderr(cname)
    if not running:
        detail = f"\n{inspect_err}" if inspect_err else ""
        print(
            f"Error: remote box '{cname}' did not come up on '{dest}'.{detail}",
            file=sys.stderr,
        )
        return 1

    # (f) Best-effort seed of the LOCAL attached-container config (never blocks).
    _seed_remote_attached_config(engine, cname)

    # (g)+(h) Build the attach URI with the routing token and launch VS Code.
    uri = _attach_uri(cname, context=context)
    print(f"Opening VS Code attached to remote box '{cname}' on '{dest}'...")
    subprocess.run([code_bin, "--folder-uri", uri])
    return 0

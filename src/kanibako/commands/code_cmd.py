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
import shutil
import subprocess
import sys

from kanibako.config import config_file_path, load_config
from kanibako.container import ContainerRuntime
from kanibako.errors import ContainerError, KanibakoError
from kanibako.log import get_logger
from kanibako.paths import (
    xdg,
    load_std_paths,
    resolve_box_target,
)
from kanibako.settings_resolve import GUEST_HOME
from kanibako.utils import container_name_for
from kanibako.vscode_config import (
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

    proj = resolve_box_target(std, config, project_dir, initialize=False)
    cname = container_name_for(proj)

    # Fail fast if the host `code` CLI is missing — BEFORE auto-starting a box, so
    # a missing prerequisite never leaves a background box behind.
    code_bin = shutil.which("code")
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
        # Re-resolve: a brand-new box was just materialised (name/registration now
        # exist), so recompute proj/cname before seeding + attaching.
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


def _resolve_box_agent_name(runtime, std, proj, container_name: str) -> str | None:
    """Best-effort: the RUNNING box's authoritative agent NODE-name (or ``None``).

    STAMP-FIRST, mirroring ``stop.py._writeback_on_stop`` and ``start.py``'s
    reattach fast-source: a running box's authoritative agent is its
    ``KANIBAKO_AGENT`` launch stamp (``runtime.inspect_env``), NOT the create-time
    cascade.  Using the stamp avoids two cascade mis-resolutions on a running box:
    (1) 2+ installed agents + no system default → the cascade RAISES (seed nothing
    for a live claude box); (2) a system default that has since diverged from the
    box's actually-running agent → seed the WRONG agent.

    Falls back to the ``resolve_agent`` create-cascade ONLY for pre-stamp (older)
    boxes with no ``KANIBAKO_AGENT`` env.  Swallows every failure → ``None``.
    NEVER raises.  Resolved ONCE per ``code`` invocation and consumed by the
    extension seed (:func:`_resolve_box_vscode_extension`) so the box is inspected
    a single time.
    """
    try:
        stamp = runtime.inspect_env(container_name, "KANIBAKO_AGENT")
        if stamp:
            return stamp

        # Pre-stamp (older) box: fall back to the create-time resolve_agent cascade.
        from kanibako.config import (
            BOX_META_FILE,
            load_merged_config,
            resolve_agent,
        )
        from kanibako.paths import workset_settings_path

        merged = load_merged_config(
            config_file_path(xdg("XDG_CONFIG_HOME", ".config")),
            proj.metadata_path / BOX_META_FILE,
            workset_path=workset_settings_path(proj.group),
        )
        return resolve_agent(
            explicit_agent=None,
            box_agent_name=merged.box_agent_name,
            workset_agent=None,
            system_default_path=std.settings,
            project_path=proj.project_path,
        )
    except Exception:
        get_logger("code").debug(
            "could not resolve box agent name; seeding none",
            exc_info=True,
        )
        return None


def _resolve_box_vscode_extension(agent_name: str | None, proj) -> str | None:
    """Best-effort: *agent_name*'s ``descriptor.vscode_extension`` (or ``None``).

    Takes the pre-resolved box agent NODE-name (see :func:`_resolve_box_agent_name`)
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
    """
    image = runtime.container_image(container_name)
    if image:
        return image
    try:
        from kanibako.config import BOX_META_FILE, load_merged_config
        from kanibako.paths import workset_settings_path

        merged = load_merged_config(
            config_file_path(xdg("XDG_CONFIG_HOME", ".config")),
            proj.metadata_path / BOX_META_FILE,
            workset_path=workset_settings_path(proj.group),
        )
        return merged.box_image or None
    except Exception:
        get_logger("code").debug(
            "could not resolve box image; skipping attached-config seed",
            exc_info=True,
        )
        return None


def _seed_attached_config(runtime, std, proj, container_name: str) -> None:
    """Best-effort seed of the box's attached-container config. NEVER raises.

    UNION-MERGES the box workspace + the box agent's editor extension into the
    IMAGE-keyed devcontainer.json-subset VS Code reads on attach (preserving
    everything VS Code/the user already wrote).  Any failure — image resolution,
    agent resolution, filesystem — is logged at debug and swallowed so the
    `code` launch is unaffected (Phase-1 zero-launch-delta).
    """
    try:
        image_ref = _resolve_box_image(runtime, proj, container_name)
        if image_ref is None:
            return  # can't key the image-shared config → skip, never crash
        # Resolve the box agent ONCE (STAMP-first) for the extension seed.
        agent_name = _resolve_box_agent_name(runtime, std, proj, container_name)
        extension = _resolve_box_vscode_extension(agent_name, proj)
        path = attached_container_config_path(
            image_ref, xdg("XDG_CONFIG_HOME", ".config"),
        )
        seed_attached_container_config(
            path,
            workspace_folder=GUEST_HOME + "/workspace",
            extension=extension,
        )
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
        seed_attached_container_config(
            path,
            workspace_folder=GUEST_HOME + "/workspace",
            extension=extension,
        )
    except Exception:
        get_logger("code").debug(
            "failed to seed remote VS Code attached-container config",
            exc_info=True,
        )


def _run_code_remote(args: argparse.Namespace, dest: str) -> int:
    """`kanibako code --remote <host> <box>`: attach LOCAL VS Code to a REMOTE box.

    A' topology: local VS Code drives the remote rootless podman socket over an
    ssh mux; kanibako lifecycle runs on the remote host over plain ssh.  See
    :mod:`kanibako.vscode_remote`.
    """
    from kanibako import vscode_remote as vr

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
    code_bin = shutil.which("code")
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

    # (d) Lifecycle: start the remote box DETACHED and read back its cname.
    result = vr.remote_run_kanibako(
        dest, ["start", "--detach", "--print-container", str(box)],
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
        elif "print-container" in low or "unrecognized arguments" in low:
            hint = (
                "\n  Hint: the remote kanibako is too old for "
                "--print-container; upgrade it (needs >= 1.7.0)."
            )
        print(
            f"Error: remote 'kanibako start --detach' failed on '{dest}'.\n"
            f"{stderr}{hint}",
            file=sys.stderr,
        )
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

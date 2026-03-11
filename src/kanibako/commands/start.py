"""kanibako start / shell: container launch with credential flow."""

from __future__ import annotations

import argparse
import fcntl
import os
import shutil
import subprocess
import sys
from pathlib import Path

from kanibako.agents import agent_toml_path, load_agent_config, write_agent_config
from kanibako.config import config_file_path, load_config, load_merged_config
from kanibako.container import ContainerRuntime
from kanibako.errors import ContainerError
from kanibako.log import get_logger
from kanibako.paths import (
    ProjectMode,
    _upgrade_shell,
    xdg,
    load_std_paths,
    resolve_any_project,
)
from kanibako.targets import resolve_target
from kanibako.utils import container_name_for, short_hash


def add_start_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "start",
        help="Start or continue an agent session (default)",
        description="Start or continue an agent session in a container.",
    )

    # Start mode: -N/-C/-R mutually exclusive
    mode_group = p.add_mutually_exclusive_group()
    mode_group.add_argument(
        "-N", "--new", action="store_true", dest="new_session",
        help="Start a new conversation (skip default --continue)",
    )
    mode_group.add_argument(
        "-C", "--continue", action="store_true", dest="continue_session",
        help="Continue the most recent conversation (default for existing projects)",
    )
    mode_group.add_argument(
        "-R", "--resume", action="store_true", dest="resume_session",
        help="Resume with conversation picker",
    )

    # Agent mode: -A/-S mutually exclusive
    agent_group = p.add_mutually_exclusive_group()
    agent_group.add_argument(
        "-A", "--autonomous", action="store_true",
        help="Run with full permissions (--dangerously-skip-permissions)",
    )
    agent_group.add_argument(
        "-S", "--secure", action="store_true",
        help="Run without --dangerously-skip-permissions",
    )

    p.add_argument(
        "-M", "--model", default=None,
        help="Override the agent model for this run",
    )
    p.add_argument(
        "-e", "--env", action="append", default=None, metavar="KEY=VALUE",
        help="Set per-run environment variable (repeatable)",
    )
    p.add_argument(
        "--image", default=None,
        help="Use IMAGE as the container image for this run",
    )
    p.add_argument(
        "--entrypoint", default=None,
        help="Use CMD as the container entrypoint",
    )

    # Session persistence mode
    persist_group = p.add_mutually_exclusive_group()
    persist_group.add_argument(
        "--persistent", action="store_true",
        help="Run in a persistent tmux session (reattach on subsequent start)",
    )
    persist_group.add_argument(
        "--ephemeral", action="store_true",
        help="Run in foreground without tmux (single-use session)",
    )

    p.add_argument(
        "--no-helpers", action="store_true",
        help="Disable helper spawning (no hub socket mounted)",
    )
    p.add_argument(
        "--no-auto-auth", action="store_true",
        help="Disable automated browser-based OAuth refresh",
    )
    p.add_argument(
        "--browser", action="store_true",
        help="Launch a headless browser sidecar (BROWSER_WS_ENDPOINT injected)",
    )
    p.add_argument(
        "--share-images", action="store_true",
        help="Share host container image storage with child (read-only, experimental)",
    )
    p.add_argument(
        "agent_args", nargs=argparse.REMAINDER,
        help="Arguments passed directly to the agent (after --)",
    )
    p.set_defaults(func=run_start)


def add_shell_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "shell",
        help="Open a bash shell in the container",
        description="Open a bash shell in the container (no agent).",
    )
    p.add_argument(
        "-e", "--env", action="append", default=None, metavar="KEY=VALUE",
        help="Set per-run environment variable (repeatable)",
    )
    p.add_argument(
        "--image", default=None,
        help="Use IMAGE as the container image for this run",
    )
    p.add_argument(
        "--entrypoint", default=None,
        help="Use CMD as the container entrypoint",
    )

    # Session persistence mode
    persist_group = p.add_mutually_exclusive_group()
    persist_group.add_argument(
        "--persistent", action="store_true",
        help="Run in a persistent tmux session (reattach on subsequent start)",
    )
    persist_group.add_argument(
        "--ephemeral", action="store_true",
        help="Run in foreground without tmux (single-use session)",
    )

    p.add_argument(
        "--no-helpers", action="store_true",
        help="Disable helper spawning (no hub socket mounted)",
    )
    p.add_argument(
        "--share-images", action="store_true",
        help="Share host container image storage with child (read-only, experimental)",
    )
    p.add_argument(
        "shell_args", nargs=argparse.REMAINDER,
        help="Command to run (after --): kanibako shell -- echo hello",
    )
    p.set_defaults(func=run_shell)


def run_start(args: argparse.Namespace) -> int:
    entrypoint = getattr(args, "entrypoint", None)
    image_override = getattr(args, "image", None)
    new_session = getattr(args, "new_session", False)
    resume_session = getattr(args, "resume_session", False)
    secure = getattr(args, "secure", False)
    model_override = getattr(args, "model", None)
    no_helpers = getattr(args, "no_helpers", False)
    no_auto_auth = getattr(args, "no_auto_auth", False)
    browser = getattr(args, "browser", False)
    share_images = getattr(args, "share_images", False)
    explicit_persistent = getattr(args, "persistent", False)
    explicit_ephemeral = getattr(args, "ephemeral", False)
    if explicit_persistent:
        persistent = True
    elif explicit_ephemeral:
        persistent = False
    else:
        # Default: persistent when tmux is available
        persistent = _tmux_available()
    env_vars = getattr(args, "env", None) or []
    agent_args = getattr(args, "agent_args", [])

    # Extract [project] positional from agent_args: if the first arg is not
    # "--" and doesn't start with "-", treat it as the project directory.
    project_dir = getattr(args, "project", None)
    if project_dir is None and agent_args and agent_args[0] != "--" and not agent_args[0].startswith("-"):
        project_dir = agent_args[0]
        agent_args = agent_args[1:]

    # Strip leading '--' from REMAINDER
    if agent_args and agent_args[0] == "--":
        agent_args = agent_args[1:]

    # Map -A/-S to safe_mode: -A means autonomous (safe_mode=False),
    # -S means secure (safe_mode=True). Neither means autonomous (default).
    safe_mode = secure

    return _run_container(
        project_dir=project_dir,
        entrypoint=entrypoint,
        image_override=image_override,
        new_session=new_session,
        safe_mode=safe_mode,
        resume_mode=resume_session,
        extra_args=agent_args,
        no_helpers=no_helpers,
        no_auto_auth=no_auto_auth,
        browser=browser,
        share_images=share_images,
        persistent=persistent,
        model_override=model_override,
        cli_env=env_vars,
    )


def run_shell(args: argparse.Namespace) -> int:
    shell_args = getattr(args, "shell_args", [])

    # Extract [project] positional from shell_args: if the first arg is not
    # "--" and doesn't start with "-", treat it as the project directory.
    project_dir = getattr(args, "project", None)
    if project_dir is None and shell_args and shell_args[0] != "--" and not shell_args[0].startswith("-"):
        project_dir = shell_args[0]
        shell_args = shell_args[1:]

    # Strip leading '--' from REMAINDER
    if shell_args and shell_args[0] == "--":
        shell_args = shell_args[1:]
    entrypoint = getattr(args, "entrypoint", None)
    if not entrypoint:
        entrypoint = "/bin/sh" if shell_args else "/bin/bash"
    # Wrap shell_args as -c "cmd" so /bin/sh executes them as a command
    if shell_args and not getattr(args, "entrypoint", None):
        shell_args = ["-c", " ".join(shell_args)]

    image_override = getattr(args, "image", None)
    no_helpers = getattr(args, "no_helpers", False)
    share_images = getattr(args, "share_images", False)
    env_vars = getattr(args, "env", None) or []

    explicit_persistent = getattr(args, "persistent", False)
    explicit_ephemeral = getattr(args, "ephemeral", False)
    if explicit_persistent:
        persistent = True
    elif explicit_ephemeral:
        persistent = False
    else:
        persistent = False  # shell defaults to ephemeral

    return _run_container(
        project_dir=project_dir,
        entrypoint=entrypoint,
        image_override=image_override,
        new_session=False,
        safe_mode=False,
        resume_mode=False,
        extra_args=shell_args,
        no_helpers=no_helpers,
        share_images=share_images,
        persistent=persistent,
        cli_env=env_vars,
    )


def _tmux_available() -> bool:
    """Check if tmux is installed."""
    return shutil.which("tmux") is not None


def _tmux_session_name(project_name: str) -> str:
    """Generate a deterministic tmux session name for host-side reattach."""
    return f"kanibako-{project_name}"


def _tmux_has_session(session_name: str) -> bool:
    """Check if a tmux session exists on the host."""
    return subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        capture_output=True,
    ).returncode == 0


def _apply_tweakcc(install, agent_cfg, cache_path, logger):
    """Apply tweakcc patching if enabled in agent config.

    Returns ``(patched_install, cache_entry, cache)`` on success, or
    *None* if tweakcc is disabled or patching fails (graceful fallback).
    """
    from kanibako.bun_sea import BunSEAError, cli_js_hash
    from kanibako.targets.base import AgentInstall
    from kanibako.tweakcc import build_merged_config, resolve_tweakcc_config, write_merged_config
    from kanibako.tweakcc_cache import TweakccCache, TweakccCacheError, config_hash

    tweakcc_cfg = resolve_tweakcc_config(agent_cfg.tweakcc)
    if not tweakcc_cfg.enabled:
        return None

    try:
        merged_config = build_merged_config(tweakcc_cfg)
        bin_hash = cli_js_hash(install.binary)
        cfg_hash = config_hash(merged_config)

        cache_dir = cache_path / "tweakcc"
        cache = TweakccCache(cache_dir)
        key = cache.cache_key(bin_hash, cfg_hash)

        entry = cache.get(key)
        if entry is None:
            config_file = cache_dir / f".config-{key}.json"
            write_merged_config(merged_config, config_file)
            tweakcc_cmd = ["tweakcc", "--apply", "--config", str(config_file)]
            entry = cache.put(key, install.binary, tweakcc_cmd)
            logger.info("Patched binary cached: %s", key)
        else:
            logger.info("Using cached patched binary: %s", key)

        patched_install = AgentInstall(
            name=install.name,
            binary=entry.path,
            install_dir=install.install_dir,
        )
        return patched_install, entry, cache

    except (BunSEAError, TweakccCacheError) as exc:
        logger.warning(
            "tweakcc patching failed, using unpatched binary: %s", exc,
        )
        return None


def _run_container(
    *,
    project_dir: str | None,
    entrypoint: str | None,
    image_override: str | None,
    new_session: bool,
    safe_mode: bool,
    resume_mode: bool,
    extra_args: list[str],
    no_helpers: bool = False,
    no_auto_auth: bool = False,
    browser: bool = False,
    share_images: bool = False,
    persistent: bool = False,
    model_override: str | None = None,
    cli_env: list[str] | None = None,
    _is_retry: bool = False,
) -> int:
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)

    std = load_std_paths(config)

    proj = resolve_any_project(std, config, project_dir, initialize=True)

    # Hint about orphaned project data when initializing a new project
    if proj.is_new and proj.mode == ProjectMode.local:
        from kanibako.paths import iter_projects
        for _settings, _ppath in iter_projects(std, config):
            if _ppath is not None and not _ppath.is_dir():
                print(
                    "hint: orphaned project data detected — "
                    "run 'kanibako box list' or use 'kanibako box migrate' "
                    "if you moved a project.",
                    file=sys.stderr,
                )
                break

    # Load merged config (global + project)
    project_toml = proj.metadata_path / "project.toml"
    merged = load_merged_config(
        config_file,
        project_toml,
        cli_overrides={"container_image": image_override} if image_override else None,
    )

    image = merged.container_image

    # Persist image override for new projects so it becomes the default
    if proj.is_new and image_override:
        from kanibako.config import write_project_config
        write_project_config(project_toml, image_override)

    # Detect container runtime and ensure image is available
    try:
        runtime = ContainerRuntime()
    except ContainerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    containers_dir = std.data_path / "containers"
    try:
        runtime.ensure_image(image, containers_dir)
    except ContainerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    from kanibako.freshness import check_image_freshness
    check_image_freshness(runtime, image, std.cache_path)

    # Resolve target (agent plugin) and detect installation
    logger = get_logger("start")
    is_agent_mode = entrypoint is None
    target = None
    install = None
    if is_agent_mode:
        try:
            target = resolve_target(merged.target_name or None)
        except KeyError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        logger.debug("Resolved target: %s", target.display_name)
        install = target.detect()
        if install:
            print(
                f"Using host {target.display_name}: {install.binary}",
                file=sys.stderr,
            )
        elif target.has_binary:
            print(
                f"Warning: {target.display_name} binary not found on host. "
                f"Launching without agent.",
                file=sys.stderr,
            )
            logger.debug("target.detect() returned None for %s", target.name)

    # Load agent config
    agent_id = target.name if target else "general"
    agent_cfg_path = agent_toml_path(std.data_path, agent_id, merged.paths_agents)
    if target and not agent_cfg_path.exists():
        # First-use: generate default agent config from target plugin
        agent_cfg = target.generate_agent_config()
        write_agent_config(agent_cfg_path, agent_cfg)
    else:
        agent_cfg = load_agent_config(agent_cfg_path)

    # Deterministic container name for stop/cleanup
    container_name = container_name_for(proj)

    logger.debug("Project: %s (mode=%s)", proj.project_path, proj.mode)
    logger.debug("Image: %s", image)
    logger.debug("Container: %s", container_name)

    # Persistent mode: reattach if already running, clean up stale containers
    if persistent:
        if runtime.is_running(container_name):
            # Refresh credentials before reattaching
            if target and proj.auth != "distinct":
                target.refresh_credentials(proj.shell_path)
            return runtime.exec(
                container_name, ["tmux", "attach", "-t", "kanibako"]
            )
        # Stale stopped container: remove before recreating
        if runtime.container_exists(container_name):
            runtime.rm(container_name)
        # Persistent mode forces no helpers
        no_helpers = True
    else:
        # Interactive mode: guard against existing persistent container
        if runtime.container_exists(container_name):
            print(
                "Error: A container already exists for this project.\n"
                "If a persistent session is running, use 'kanibako start' to\n"
                "reattach, or 'kanibako stop' to end it.",
                file=sys.stderr,
            )
            return 1

    # Concurrency lock (skip for persistent — container existence is the lock)
    lock_fd = None
    if not persistent:
        lock_file = proj.metadata_path / ".kanibako.lock"
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = open(lock_file, "w")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(
                "Error: Another kanibako instance is running for this project.",
                file=sys.stderr,
            )
            lock_fd.close()
            return 1

        # Record container name so `kanibako stop` can find it
        lock_fd.write(container_name + "\n")
        lock_fd.flush()

    try:
        # Auto-snapshot vault share-rw before launch.
        if proj.vault_enabled and proj.vault_rw_path.is_dir():
            from kanibako.snapshots import auto_snapshot
            snap = auto_snapshot(proj.vault_rw_path)
            if snap:
                print(f"Vault snapshot: {snap.name}", file=sys.stderr)

        # Upgrade shell (add shell.d support to existing shells).
        _upgrade_shell(proj.shell_path)

        # Shell directory hygiene: remove waste files, compress old logs.
        from kanibako.hygiene import cleanup_shell_dir
        hygiene_actions = cleanup_shell_dir(proj.shell_path)
        if hygiene_actions:
            for action in hygiene_actions:
                logger.info(action)

        # Template application + agent init for new projects.
        if proj.is_new and target:
            from kanibako.templates import apply_shell_template
            templates_base = std.data_path / merged.paths_templates
            # Ensure the agent-specific template variant directory exists.
            (templates_base / target.name / agent_cfg.shell).mkdir(parents=True, exist_ok=True)
            apply_shell_template(proj.shell_path, templates_base, target.name, agent_cfg.shell)
            target.init_home(proj.shell_path, auth=proj.auth)

            # Merge layered instruction files (base + template + user).
            instr_files = target.instruction_files()
            if instr_files:
                from kanibako.instructions import merge_instruction_files
                merge_instruction_files(
                    shell_path=proj.shell_path,
                    config_dir_name=target.config_dir_name,
                    instruction_files=instr_files,
                    templates_base=templates_base,
                    agent_name=target.name,
                    template_name=agent_cfg.shell,
                )

        # Automated OAuth refresh (before interactive check_auth)
        if (
            target
            and install
            and proj.auth != "distinct"
            and not no_auto_auth
            and target.name == "claude"
        ):
            try:
                from kanibako.auth_browser import auto_refresh_auth

                auto_result = auto_refresh_auth(
                    str(install.binary), std.data_path
                )
                if auto_result.success:
                    logger.info("Auto-auth succeeded")
                else:
                    logger.debug("Auto-auth skipped: %s", auto_result.error)
            except Exception as exc:
                logger.debug("Auto-auth failed: %s", exc)

        # Pre-launch auth check (skip for distinct auth — creds live in project)
        if target and install and proj.auth != "distinct":
            if not target.check_auth():
                print("Error: Authentication failed.", file=sys.stderr)
                return 1

        # Credential refresh via target (skip for distinct auth)
        if target and proj.auth != "distinct":
            target.refresh_credentials(proj.shell_path)

        # tweakcc: patch agent binary if enabled
        tweakcc_entry = None
        tweakcc_cache_obj = None
        if target and install and agent_cfg.tweakcc:
            result = _apply_tweakcc(install, agent_cfg, std.cache_path, logger)
            if result:
                install, tweakcc_entry, tweakcc_cache_obj = result

        # Build CLI args via target, merging agent default_args and state
        if target:
            effective_state = _build_effective_state(target, agent_cfg, project_toml)
            # Apply model override from -M/--model flag
            if model_override:
                effective_state["model"] = model_override
            state_args, state_env = target.apply_state(effective_state)
            all_extra = list(agent_cfg.default_args) + list(extra_args)
            cli_args = target.build_cli_args(
                safe_mode=safe_mode,
                resume_mode=resume_mode,
                new_session=new_session,
                is_new_project=proj.is_new,
                extra_args=all_extra,
            )
            cli_args.extend(state_args)
        else:
            state_env = {}
            cli_args = list(extra_args)

        # Build extra mounts from target binary detection
        extra_mounts = []
        if target and install:
            binary_mnts = target.binary_mounts(install)
            if not binary_mnts:
                print(
                    f"Error: {target.display_name} binary detected at "
                    f"{install.binary} but mount sources are missing.\n"
                    f"  binary:      {install.binary} "
                    f"({'exists' if install.binary.exists() else 'MISSING'})\n"
                    f"  install_dir: {install.install_dir} "
                    f"({'exists' if install.install_dir.exists() else 'MISSING'})\n"
                    f"The container would launch without the agent binary.",
                    file=sys.stderr,
                )
                return 1
            extra_mounts.extend(binary_mnts)

        # kanibako CLI bind-mount (package + entry script)
        kanibako_mnts = _kanibako_mounts()
        extra_mounts.extend(kanibako_mnts)

        # Shared cache mounts (global, lazy — only mount if dir exists)
        if proj.global_shared_path:
            from kanibako.targets.base import Mount
            for cache_name, container_rel in merged.shared_caches.items():
                host_dir = proj.global_shared_path / cache_name
                if host_dir.is_dir():
                    extra_mounts.append(Mount(
                        source=host_dir,
                        destination=f"/home/agent/{container_rel}",
                        options="Z,U",
                    ))

        # Agent-level shared cache mounts (lazy — only mount if dir exists)
        if proj.local_shared_path and agent_cfg.shared_caches:
            from kanibako.targets.base import Mount as _Mount
            for cache_name, container_rel in agent_cfg.shared_caches.items():
                host_dir = proj.local_shared_path / agent_id / cache_name
                if host_dir.is_dir():
                    extra_mounts.append(_Mount(
                        source=host_dir,
                        destination=f"/home/agent/{container_rel}",
                        options="Z,U",
                    ))

        # Resource scope mounts (SHARED / SEEDED from target.resource_mappings())
        if target and proj.global_shared_path:
            resource_mounts = _build_resource_mounts(proj, target, agent_id)
            extra_mounts.extend(resource_mounts)

        # Image sharing: mount host image storage read-only into child.
        if share_images or merged.share_images:
            from kanibako.image_sharing import build_image_sharing_mounts
            staging = proj.metadata_path / ".image-sharing"
            img_mounts = build_image_sharing_mounts(
                runtime.cmd, staging,
            )
            if img_mounts:
                extra_mounts.extend(img_mounts)
                logger.info("Image sharing enabled: %d mounts added", len(img_mounts))
            else:
                print(
                    "Warning: --share-images enabled but host image storage "
                    "could not be detected. Continuing without image sharing.",
                    file=sys.stderr,
                )

        # Peer communication: mount shared comms directory.
        from kanibako.targets.base import Mount as _CMount
        comms_path = Path(merged.paths_comms)
        if not comms_path.is_absolute():
            comms_path = std.data_path / comms_path
        comms_path.mkdir(parents=True, exist_ok=True)
        if proj.name:
            mailbox = comms_path / "mailbox" / proj.name
            mailbox.mkdir(parents=True, exist_ok=True)
        broadcast = comms_path / "broadcast.log"
        if not broadcast.exists():
            broadcast.touch()
        _rotate_file(broadcast)
        extra_mounts.append(
            _CMount(comms_path, "/home/agent/comms", "Z,U"),
        )

        # Read per-project and global environment variables.
        from kanibako.shellenv import merge_env
        global_env_path = std.data_path / "env"
        project_env_path = proj.metadata_path / "env"
        container_env: dict[str, str] = merge_env(global_env_path, project_env_path) or {}
        container_env.update(agent_cfg.env)
        container_env.update(state_env)

        # Merge per-run -e/--env KEY=VALUE vars (highest priority).
        if cli_env:
            for item in cli_env:
                if "=" in item:
                    k, v = item.split("=", 1)
                    container_env[k] = v

        # Disable Claude Code telemetry inside containers.
        if target and target.name == "claude":
            container_env.setdefault(
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1",
            )

        # Inject instance identity for peer communication.
        if proj.name:
            container_env["KANIBAKO_NAME"] = proj.name

        # Helper hub: start listener before director, mount socket
        hub = None
        helpers_enabled = not no_helpers and not merged.helpers_disabled
        if helpers_enabled:
            from kanibako.helper_listener import HelperContext, HelperHub, MessageLog
            from kanibako.targets.base import Mount as _HMount

            # Socket must live in a short path to stay under the 108-byte
            # AF_UNIX limit.  /run/user/$UID is the XDG runtime dir.
            _uid = os.getuid()
            _run_base = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{_uid}"))
            _run_dir = _run_base / "kanibako"
            try:
                _run_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                # Fallback if /run/user/$UID is not writable
                _run_dir = Path(f"/tmp/kanibako-{_uid}")
                _run_dir.mkdir(parents=True, exist_ok=True)
            _sock_id = proj.name if proj.name else short_hash(proj.project_hash)
            socket_path = _run_dir / f"{_sock_id}.sock"
            validate_socket_path(socket_path)
            _log_id = proj.name if proj.name else short_hash(proj.project_hash)
            log_dir = std.data_path / "logs" / _log_id
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "helper-messages.jsonl"

            # Ensure helpers/ dir exists in shell_path
            helpers_dir = proj.shell_path / "helpers"
            helpers_dir.mkdir(exist_ok=True)

            # Build context for helper container launches
            binary_mounts = list(kanibako_mnts)
            if target and install:
                binary_mounts.extend(target.binary_mounts(install))

            # Share tweakcc cache with helpers so they reuse patched binaries
            if tweakcc_entry is not None:
                _tweakcc_cache_dir = std.cache_path / "tweakcc"
                if _tweakcc_cache_dir.is_dir():
                    binary_mounts.append(_HMount(
                        source=_tweakcc_cache_dir,
                        destination=str(_tweakcc_cache_dir),
                        options="ro",
                    ))

            helper_ctx = HelperContext(
                runtime=runtime,
                image=image,
                container_name_prefix=container_name,
                shell_path=proj.shell_path,
                helpers_dir=helpers_dir,
                socket_path=socket_path,
                binary_mounts=binary_mounts,
                env=container_env,
                entrypoint=entrypoint,
                default_entrypoint=target.default_entrypoint if target else None,
                project_path=proj.project_path,
                data_path=std.data_path,
            )

            msg_log = MessageLog(log_path)
            hub = HelperHub()
            hub.start(socket_path, helper_ctx, log=msg_log)

            # Mount the socket into the container (only if hub started)
            kanibako_dir = proj.shell_path / ".kanibako"
            kanibako_dir.mkdir(exist_ok=True)
            if socket_path.exists():
                extra_mounts.append(_HMount(
                    source=socket_path,
                    destination="/home/agent/.kanibako/helper.sock",
                    options="",
                ))

            # Mount helper-messages.jsonl for log command inside container
            if log_path.exists():
                extra_mounts.append(_HMount(
                    source=log_path,
                    destination="/home/agent/.kanibako/helper-messages.jsonl",
                    options="ro",
                ))

        # Pre-launch validation: warn about missing mount sources.
        _validate_mounts(extra_mounts, logger)

        # Browser sidecar: on-demand headless Chrome for agent web access
        browser_sidecar = None
        if browser:
            try:
                from kanibako.browser_sidecar import (
                    BrowserSidecar,
                    ws_endpoint_for_container,
                )

                sidecar_name = f"{container_name}-browser"
                browser_sidecar = BrowserSidecar(
                    runtime=runtime,
                    container_name=sidecar_name,
                )
                ws_url = browser_sidecar.start()
                container_ws = ws_endpoint_for_container(ws_url)
                container_env["BROWSER_WS_ENDPOINT"] = container_ws
                logger.info("Browser sidecar started: %s", container_ws)
            except Exception as exc:
                logger.warning("Browser sidecar failed to start: %s", exc)
                browser_sidecar = None

        # Set agent entrypoint if not explicitly overridden.
        if not entrypoint and target:
            entrypoint = target.default_entrypoint

        # Persistent mode: wrap command with tmux
        if persistent:
            inner_cmd = entrypoint or "/bin/bash"
            tmux_args = ["new-session", "-s", "kanibako", "--", inner_cmd]
            if cli_args:
                tmux_args.extend(cli_args)
            entrypoint = "tmux"
            cli_args = tmux_args

        try:
            # Run the container
            rc = runtime.run(
                image,
                shell_path=proj.shell_path,
                project_path=proj.project_path,
                vault_ro_path=proj.vault_ro_path,
                vault_rw_path=proj.vault_rw_path,
                extra_mounts=extra_mounts or None,
                vault_tmpfs=(proj.mode == ProjectMode.local),
                vault_enabled=proj.vault_enabled,
                env=container_env,
                name=container_name,
                entrypoint=entrypoint,
                cli_args=cli_args or None,
                detach=persistent,
            )
        finally:
            # Stop helper hub after director exits
            if hub is not None:
                hub.stop()
            # Release tweakcc cache entry (shared lock)
            if tweakcc_entry is not None and tweakcc_cache_obj is not None:
                tweakcc_cache_obj.release(tweakcc_entry)
            # Stop browser sidecar
            if browser_sidecar is not None:
                try:
                    browser_sidecar.stop()
                except Exception as exc:
                    logger.debug("Browser sidecar cleanup: %s", exc)

        if persistent:
            # Attach to the new tmux session
            rc = runtime.exec(
                container_name, ["tmux", "attach", "-t", "kanibako"]
            )
            # If agent exited, show container logs so the user can
            # see why (tmux swallows output on exit).
            if not runtime.is_running(container_name):
                logs = _container_logs(runtime, container_name)
                if logs:
                    print(logs, file=sys.stderr)
                    # Auto-retry as new session if the target says so
                    # (once only — _is_retry prevents loops).
                    if (
                        target
                        and not new_session
                        and not _is_retry
                        and target.should_retry_new_session(logs)
                    ):
                        print(
                            "Restarting with a new session.",
                            file=sys.stderr,
                        )
                        runtime.rm(container_name)
                        return _run_container(
                            project_dir=project_dir,
                            entrypoint=None,
                            image_override=image_override,
                            new_session=True,
                            safe_mode=safe_mode,
                            resume_mode=False,
                            extra_args=extra_args,
                            no_helpers=no_helpers,
                            no_auto_auth=no_auto_auth,
                            browser=browser,
                            share_images=share_images,
                            persistent=persistent,
                            model_override=model_override,
                            cli_env=cli_env,
                            _is_retry=True,
                        )
        else:
            # Write back refreshed credentials via target (skip for distinct auth)
            if target and proj.auth != "distinct":
                target.writeback_credentials(proj.shell_path)

            # Hint when agent exits non-zero and --continue/--resume was used
            if rc != 0 and is_agent_mode and not new_session:
                print(
                    "hint: if the agent exited because there was no conversation "
                    "to continue, use 'kanibako start -N' to start fresh.",
                    file=sys.stderr,
                )

        return rc

    finally:
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()


def _build_effective_state(target, agent_cfg, project_toml) -> dict[str, str]:
    """Merge target defaults, agent config state, and project overrides.

    Resolution order (highest wins):
      1. Project overrides (``[target_settings]`` in project.toml)
      2. Agent config state (``[state]`` in agent TOML)
      3. Target defaults (from ``setting_descriptors()``)

    Undeclared keys in agent state are passed through unchanged.
    """
    from kanibako.config import read_target_settings

    descriptors = target.setting_descriptors()
    if not descriptors:
        return dict(agent_cfg.state)

    try:
        project_overrides = read_target_settings(project_toml)
    except Exception:
        project_overrides = {}

    # Start with target defaults.
    effective: dict[str, str] = {d.key: d.default for d in descriptors}

    # Layer agent config state (all keys, including undeclared).
    effective.update(agent_cfg.state)

    # Layer project overrides (only declared keys survive validation at CLI).
    effective.update(project_overrides)

    return effective


def _kanibako_mounts():
    """Build bind mounts for the kanibako CLI inside containers.

    Returns two mounts:
      1. Package dir → /opt/kanibako/kanibako/ (ro)
      2. Entry script → /home/agent/.local/bin/kanibako (ro)
    """
    import importlib.resources

    import kanibako
    from kanibako.targets.base import Mount

    pkg_dir = Path(kanibako.__file__).parent

    entry_ref = importlib.resources.files("kanibako.scripts").joinpath("kanibako-entry")
    entry_path = Path(str(entry_ref))

    return [
        Mount(pkg_dir, "/opt/kanibako/kanibako", "ro"),
        Mount(entry_path, "/home/agent/.local/bin/kanibako", "ro"),
    ]


def _build_resource_mounts(proj, target, agent_id: str):
    """Build bind mounts from target resource_mappings() and per-project overrides.

    - SHARED: mount shared dir over ``/home/agent/{config_dir}/{path}`` (read-write).
    - SEEDED: on first init, copy from shared to project-local; then no extra mount.
    - PROJECT: no extra mount (already in shell_path).
    """
    import shutil

    from kanibako.config import read_resource_overrides
    from kanibako.targets.base import Mount, ResourceScope

    mappings = target.resource_mappings()
    if not mappings:
        return []

    shared_base = proj.global_shared_path
    if not shared_base:
        return []

    config_dir = target.config_dir_name

    project_toml = proj.metadata_path / "project.toml"
    try:
        overrides = read_resource_overrides(project_toml)
    except Exception:
        overrides = {}

    mounts = []
    for mapping in mappings:
        # Apply per-project override if present.
        scope_str = overrides.get(mapping.path)
        scope = ResourceScope(scope_str) if scope_str else mapping.scope

        if scope == ResourceScope.SHARED:
            shared_path = shared_base / agent_id / mapping.path
            if mapping.path.endswith("/"):
                shared_path.mkdir(parents=True, exist_ok=True)
            else:
                # File resource: create parent dir and touch the file.
                shared_path.parent.mkdir(parents=True, exist_ok=True)
                if not shared_path.exists():
                    shared_path.touch()
            mounts.append(Mount(
                source=shared_path,
                destination=f"/home/agent/{config_dir}/{mapping.path}",
                options="Z,U",
            ))
        elif scope == ResourceScope.SEEDED:
            local = proj.shell_path / config_dir / mapping.path
            if not local.exists():
                src = shared_base / agent_id / mapping.path
                if src.exists():
                    if src.is_dir():
                        shutil.copytree(str(src), str(local))
                    else:
                        local.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(src), str(local))
        # PROJECT scope: no extra mount needed.

    return mounts


# AF_UNIX sun_path limit (108 on Linux, 104 on macOS).
_UNIX_SOCKET_PATH_LIMIT = 104


def _container_logs(runtime: ContainerRuntime, name: str) -> str:
    """Return recent container logs, or empty string on failure."""
    result = subprocess.run(
        [runtime.cmd, "logs", "--tail", "50", name],
        capture_output=True, text=True,
    )
    return (result.stdout + result.stderr).strip() if result.returncode == 0 else ""


def _validate_mounts(mounts: list, logger) -> None:
    """Warn about mount sources that don't exist on the host.

    Called before ``runtime.run()`` to catch issues early with a clear
    message instead of a cryptic Podman error.
    """
    for mount in mounts:
        src = mount.source
        if not src.exists():
            logger.warning("Mount source missing: %s → %s", src, mount.destination)
            print(
                f"Warning: mount source does not exist: {src}",
                file=sys.stderr,
            )


_ROTATE_MAX_BYTES = 1_048_576  # 1 MiB


def _rotate_file(path: Path) -> None:
    """Rotate *path* if it exceeds the size threshold."""
    try:
        size = path.stat().st_size
        if not isinstance(size, int) or size < _ROTATE_MAX_BYTES:
            return
    except (OSError, TypeError):
        return
    backup = path.with_suffix(path.suffix + ".1")
    path.rename(backup)
    path.touch()


def validate_socket_path(socket_path: Path) -> None:
    """Raise ValueError if *socket_path* exceeds the AF_UNIX length limit."""
    path_len = len(str(socket_path))
    if path_len >= _UNIX_SOCKET_PATH_LIMIT:
        raise ValueError(
            f"Socket path too long ({path_len} >= {_UNIX_SOCKET_PATH_LIMIT}): "
            f"{socket_path}"
        )

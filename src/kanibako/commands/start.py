"""kanibako start / shell / resume: container launch with credential flow."""

from __future__ import annotations

import argparse
import fcntl
import os
import sys
from pathlib import Path

from kanibako.agents import agent_toml_path, load_agent_config, write_agent_config
from kanibako.config import config_file_path, load_config, load_merged_config
from kanibako.container import ContainerRuntime
from kanibako.errors import ConfigError, ContainerError
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
    _add_common_args(p)
    p.add_argument(
        "-i", "--image", default=None,
        help="Use IMAGE as the container image for this run",
    )
    p.add_argument(
        "-N", "--new", action="store_true",
        help="Start a new conversation (skip default --continue)",
    )
    p.add_argument(
        "-S", "--safe", action="store_true",
        help="Run without --dangerously-skip-permissions",
    )
    p.add_argument(
        "--no-helpers", action="store_true",
        help="Disable helper spawning (no hub socket mounted)",
    )
    p.add_argument(
        "agent_args", nargs=argparse.REMAINDER,
        help="Arguments passed directly to the agent",
    )
    p.set_defaults(func=run_start)


def add_shell_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "shell",
        help="Open a bash shell in the container",
        description="Open a bash shell in the container (no agent).",
    )
    _add_common_args(p)
    p.set_defaults(func=run_shell)


def add_resume_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "resume",
        help="Resume with conversation picker",
        description="Resume a previous conversation using the agent's conversation picker.",
    )
    p.add_argument(
        "-p", "--project", default=None,
        help="Use DIR as the project directory (default: cwd)",
    )
    p.add_argument(
        "-S", "--safe", action="store_true",
        help="Run without --dangerously-skip-permissions",
    )
    p.set_defaults(func=run_resume)


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-c", "--command", dest="entrypoint", default=None,
        help="Use CMD as the container entrypoint",
    )
    parser.add_argument(
        "-E", "--entrypoint", dest="entrypoint", default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "-p", "--project", default=None,
        help="Use DIR as the project directory (default: cwd)",
    )


def run_start(args: argparse.Namespace) -> int:
    entrypoint = getattr(args, "entrypoint", None)
    image_override = getattr(args, "image", None)
    new_session = getattr(args, "new", False)
    safe_mode = getattr(args, "safe", False)
    no_helpers = getattr(args, "no_helpers", False)
    agent_args = getattr(args, "agent_args", [])
    # Strip leading '--' from REMAINDER
    if agent_args and agent_args[0] == "--":
        agent_args = agent_args[1:]

    return _run_container(
        project_dir=args.project,
        entrypoint=entrypoint,
        image_override=image_override,
        new_session=new_session,
        safe_mode=safe_mode,
        resume_mode=False,
        extra_args=agent_args,
        no_helpers=no_helpers,
    )


def run_shell(args: argparse.Namespace) -> int:
    entrypoint = getattr(args, "entrypoint", None) or "/bin/bash"
    return _run_container(
        project_dir=args.project,
        entrypoint=entrypoint,
        image_override=None,
        new_session=False,
        safe_mode=False,
        resume_mode=False,
        extra_args=[],
    )


def run_resume(args: argparse.Namespace) -> int:
    safe_mode = getattr(args, "safe", False)
    return _run_container(
        project_dir=args.project,
        entrypoint=None,
        image_override=None,
        new_session=False,
        safe_mode=safe_mode,
        resume_mode=True,
        extra_args=[],
    )


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
    persistent: bool = False,
) -> int:
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)

    try:
        std = load_std_paths(config)
    except ConfigError:
        print("kanibako hasn't been set up yet. Run setup now? [y/N] ", end="", flush=True)
        try:
            answer = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer in ("y", "yes"):
            import argparse as _argparse
            from kanibako.commands.install import run as setup_run
            setup_run(_argparse.Namespace())
            # Retry after setup
            std = load_std_paths(config)
        else:
            print("Run 'kanibako setup' when you're ready.")
            return 1

    proj = resolve_any_project(std, config, project_dir, initialize=True)

    # Hint about orphaned project data when initializing a new project
    if proj.is_new and proj.mode == ProjectMode.account_centric:
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
                "If a persistent session is running, use 'kanibako connect' to\n"
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

        # Template application + agent init for new projects.
        if proj.is_new and target:
            from kanibako.templates import apply_shell_template
            templates_base = std.data_path / merged.paths_templates
            apply_shell_template(proj.shell_path, templates_base, target.name, agent_cfg.shell)
            target.init_home(proj.shell_path, auth=proj.auth)

        # Pre-launch auth check (skip for distinct auth — creds live in project)
        if target and install and proj.auth != "distinct":
            if not target.check_auth():
                print("Error: Authentication failed.", file=sys.stderr)
                return 1

        # Credential refresh via target (skip for distinct auth)
        if target and proj.auth != "distinct":
            target.refresh_credentials(proj.shell_path)

        # Build CLI args via target, merging agent default_args and state
        if target:
            effective_state = _build_effective_state(target, agent_cfg, project_toml)
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
            extra_mounts.extend(target.binary_mounts(install))

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

        # Read per-project and global environment variables.
        from kanibako.shellenv import merge_env
        global_env_path = std.data_path / "env"
        project_env_path = proj.metadata_path / "env"
        container_env: dict[str, str] = merge_env(global_env_path, project_env_path) or {}
        container_env.update(agent_cfg.env)
        container_env.update(state_env)

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

        # Persistent mode: wrap command with tmux
        if persistent:
            inner_cmd = entrypoint or (target.default_entrypoint if target else None) or "/bin/bash"
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
                vault_tmpfs=(proj.mode == ProjectMode.account_centric),
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

        if persistent:
            # Attach to the new tmux session
            rc = runtime.exec(
                container_name, ["tmux", "attach", "-t", "kanibako"]
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


def validate_socket_path(socket_path: Path) -> None:
    """Raise ValueError if *socket_path* exceeds the AF_UNIX length limit."""
    path_len = len(str(socket_path))
    if path_len >= _UNIX_SOCKET_PATH_LIMIT:
        raise ValueError(
            f"Socket path too long ({path_len} >= {_UNIX_SOCKET_PATH_LIMIT}): "
            f"{socket_path}"
        )

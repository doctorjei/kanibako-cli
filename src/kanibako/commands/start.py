"""kanibako start / shell / resume: container launch with credential flow."""

from __future__ import annotations

import argparse
import fcntl
import sys

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
from kanibako.utils import short_hash


def add_start_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "start",
        help="Start or continue a Claude session (default)",
        description="Start or continue a Claude session in a container.",
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
        "agent_args", nargs=argparse.REMAINDER,
        help="Arguments passed directly to the agent",
    )
    p.set_defaults(func=run_start)


def add_shell_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "shell",
        help="Open a bash shell in the container",
        description="Open a bash shell in the container (no Claude agent).",
    )
    _add_common_args(p)
    p.set_defaults(func=run_shell)


def add_resume_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "resume",
        help="Resume with conversation picker",
        description="Resume a previous conversation using Claude's conversation picker.",
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
    container_name = f"kanibako-{short_hash(proj.project_hash)}"

    logger.debug("Project: %s (mode=%s)", proj.project_path, proj.mode)
    logger.debug("Image: %s", image)
    logger.debug("Container: %s", container_name)

    # Concurrency lock (known issue #3)
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
            state_args, state_env = target.apply_state(agent_cfg.state)
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

        # Read per-project and global environment variables.
        from kanibako.shellenv import merge_env
        global_env_path = std.data_path / "env"
        project_env_path = proj.metadata_path / "env"
        container_env = merge_env(global_env_path, project_env_path) or {}
        container_env.update(agent_cfg.env)
        container_env.update(state_env)
        container_env = container_env or None

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
        )

        # Write back refreshed credentials via target (skip for distinct auth)
        if target and proj.auth != "distinct":
            target.writeback_credentials(proj.shell_path)

        return rc

    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()

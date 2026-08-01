"""kanibako stop: stop running kanibako containers."""

from __future__ import annotations

import argparse
import sys

from kanibako.settings.config import config_file_path, load_config
from kanibako.runtime.container import ContainerRuntime
from kanibako.errors import ContainerError
from kanibako.settings.paths import (
    xdg,
    load_std_paths,
    resolve_box_target,
)
from kanibako.utils import container_name_for


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "stop",
        help="Stop a running kanibako container",
        description="Stop a running kanibako container for a project.",
    )
    p.add_argument(
        "project", nargs="?", default=None,
        help="Project name or path (default: cwd)",
    )
    p.add_argument(
        "--all", action="store_true", dest="all_containers",
        help="Stop all running kanibako containers",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Skip confirmation prompt (only relevant with --all)",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    try:
        runtime = ContainerRuntime()
    except ContainerError:
        print(
            "Error: No container runtime found.\n"
            "Install podman (https://podman.io/) or Docker.",
            file=sys.stderr,
        )
        return 1

    if args.all_containers:
        return _stop_all(runtime, force=getattr(args, "force", False))

    from kanibako.commands.flags import resolve_subject_value
    subject = resolve_subject_value(
        getattr(args, "project", None), getattr(args, "box", None),
    )
    return _stop_one(runtime, project_dir=subject)


def _writeback_on_stop(runtime, proj, container_name: str, *, std, config) -> None:
    """Run project -> host credential writeback for a box about to be stopped.

    Sources the box's agent from its ``KANIBAKO_AGENT`` launch stamp (set on the
    container at launch), resolves that plugin's target, and funnels through the
    shared :func:`~kanibako.commands.start.writeback_session_credentials` helper.
    Best-effort: a stop must succeed even if writeback can't run (e.g. no stamp,
    no agent, container already gone).

    Auth 3-tier SHARING: the writeback tier/source is the resolved AuthSource,
    resolved through the auth chain (single-route, the same launch-snapshot
    pipeline ``start`` uses) for the box's stamped agent — a PRIVATE box keeps its
    creds project-local and they are NOT written back.
    """
    if not runtime.is_running(container_name):
        return
    agent = runtime.inspect_env(container_name, "KANIBAKO_AGENT")
    if not agent:
        return
    try:
        from kanibako.commands.start import (
            _resolve_box_auth_source,
            writeback_session_credentials,
        )
        from kanibako.settings.agent_config import agent_settings_path
        from kanibako.agent_ref import harness_of
        from kanibako.targets import resolve_target
        # KANIBAKO_AGENT stamps the NODE-name; the target/plugin is keyed by the
        # HARNESS. ``agent_name=agent`` below keeps the node (keyspace slot).
        target = resolve_target(harness_of(agent), proj.project_path)
        # The cascade box/workset tier files are single-sourced mode-aware inside
        # the resolver (P6c) — standalone reads its file as the WORKSET tier.
        # ⚑ The §1A SELECTION LEVEL is REQUIRED (P7): ``meta.box.auth.workset_path``
        # resolves ``@workset.auth.path/@system.agent``, so without it the per-agent
        # credential dir collapses to the workset auth ROOT and this writeback would
        # land in ``<auth>/`` while the LAUNCH delivered from ``<auth>/<agent>``.
        # The ``KANIBAKO_AGENT`` stamp IS the resolved selection for a running box.
        auth_src = _resolve_box_auth_source(
            std=std,
            proj=proj,
            agent_name=agent,
            system_settings_path=std.settings,
            agent_cfg_path=agent_settings_path(std.agents, agent),
            selection_level={"system.agent": agent},
        )
        writeback_session_credentials(target, proj, auth_src=auth_src)
    except Exception:
        # Never let a writeback problem block the stop.
        pass


def _stop_one(runtime: ContainerRuntime, *, project_dir: str | None) -> int:
    """Stop the container for a single project."""
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    proj = resolve_box_target(std, config, project_dir, initialize=False)
    container_name = container_name_for(proj)

    lock_file = proj.metadata_path / ".kanibako.lock"

    # FIX 1: writeback BEFORE stopping — an in-box login must reach the host on
    # `kanibako stop` too.  The box's home is a host mount, so creds are readable
    # while the container is still up.  Source the box's agent from its launch
    # stamp (KANIBAKO_AGENT) so we know which plugin's cred lifecycle to run; a
    # box launched before stamping (or a no-agent box) has no stamp -> skip.
    _writeback_on_stop(runtime, proj, container_name, std=std, config=config)

    if runtime.stop(container_name):
        print(f"Stopped {container_name}")
        # Clean up stopped container (persistent containers lack --rm)
        if runtime.container_exists(container_name):
            runtime.rm(container_name)
    else:
        print(f"No running container found for this project ({container_name})")
        # Clean up stopped persistent container if it exists
        if runtime.container_exists(container_name):
            runtime.rm(container_name)
            print(f"Removed stopped container: {container_name}")
        else:
            print("\nIf a stale lock file is blocking a new session, remove it manually:")
            print(f"  rm {lock_file}")

    return 0


def _stop_all(runtime: ContainerRuntime, *, force: bool = False) -> int:
    """Stop all running kanibako containers."""
    containers = runtime.list_running()
    if not containers:
        print("No running kanibako containers found.")
        return 0

    # Confirmation prompt unless --force
    if not force:
        names = [name for name, _, _ in containers]
        print(f"This will stop {len(containers)} running container(s):")
        for n in names:
            print(f"  {n}")
        print()
        try:
            answer = input("Continue? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer not in ("y", "yes"):
            print("Aborted.")
            return 2

    stopped = 0
    for name, image, status in containers:
        if runtime.stop(name):
            print(f"Stopped {name}")
            # Clean up stopped container (persistent containers lack --rm)
            if runtime.container_exists(name):
                runtime.rm(name)
            stopped += 1
        else:
            print(f"Failed to stop {name}", file=sys.stderr)

    print(f"\nStopped {stopped} container(s).")
    return 0

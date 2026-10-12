"""kanibako stop: stop running kanibako containers."""

from __future__ import annotations

import argparse
import shlex
import sys

from kanibako.settings.config import user_config_file, load_config, BootstrapConfig
from kanibako.runtime.container import ContainerRuntime
from kanibako.errors import (ConfigError, ContainerError, KanibakoError, LabelQueryError,
                             ProjectError, WorksetError)
from kanibako.settings.paths import (
    ProjectPaths,
    StandardPaths,
    box_connected_workset,
    box_name_taken,
    load_std_paths,
    resolve_box_target,
)
from kanibako.settings.settings_resolve import SettingsError
from kanibako.utils import (LABEL_BOX, box_container, container_name_segments,
                            unrenderable_box_name_refusal)


def stale_lock_cure(lock_file) -> str:
    """The cure for a lock file no live box holds; the running-box refusal names it too."""
    return ("If a stale lock file is blocking a new session, remove it manually:\n"
            f"  rm {shlex.quote(str(lock_file))}")


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
        help="Stop all running kanibako containers and remove stopped ones",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Skip confirmation prompt (only relevant with --all)",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    try:
        runtime = ContainerRuntime()
    except ContainerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.all_containers:
        return _stop_all(runtime, force=getattr(args, "force", False))

    from kanibako.commands.flags import resolve_subject_value
    subject = resolve_subject_value(
        getattr(args, "project", None), getattr(args, "box", None),
    )
    return _stop_one(runtime, project_dir=subject)


def _writeback_on_stop(
    runtime, proj, container_name: str, *, std, config, box_is_live: bool,
    already_said: tuple[ConfigError | SettingsError, ...] = (),
) -> None:
    """Run project -> host credential writeback for a box about to be stopped.

    Sources the box's agent from its ``KANIBAKO_AGENT`` launch stamp (set on the
    container at launch), resolves that plugin's target, and funnels through the
    shared :func:`~kanibako.commands.start.writeback_session_credentials` helper.
    Best-effort: a stop must succeed even if writeback can't run (e.g. no stamp,
    no agent, container already gone). ⚑ The ONE failure that is SAID is the auth
    resolve refusing the box's settings (``SettingsError``, or ``ConfigError`` for a
    file that is not valid YAML) — on stderr, with the refusal's text, because it is
    the user's to fix; every other failure stays silent.

    ⚑ ``box_is_live`` is the caller's SINGLE liveness reading (``_is_live``) (P10), not a
    convenience: :func:`_stop_one` needs the same fact to pick its sentence, and
    two readings taken across the gap could disagree — writing back from a box
    the message then calls stopped, or the reverse.  Only a LIVE box is written
    back from; the box's home is a host mount, so the creds are readable while
    the container is still up.

    ⚑ ``already_said`` is the refusals :func:`_stop_one` already printed, matched
    by TEXT so one refusal is never rendered twice (P10); a refusal naming a file
    the user has not been told about is still printed whole.  The writeback is
    said either way — dropping it would be the silent credential loss it exists
    to prevent.

    Auth 3-tier SHARING: the writeback tier/source is the resolved AuthSource,
    resolved through the auth chain (single-route, the same launch-snapshot
    pipeline ``start`` uses) for the box's stamped agent — a PRIVATE box keeps its
    creds project-local and they are NOT written back.
    """
    if not box_is_live:
        return
    try:
        from kanibako.commands.start import writeback_session_credentials
        from kanibako.launch.creds_watcher import box_writeback_context, running_box_agent
        agent = running_box_agent(runtime, container_name)
        if agent is None:
            return
        # 🛑 NOT SILENT: a settings refusal leaves the user's credentials
        # un-written-back, so it is printed whole (its text names the file).
        # ⚑ SCOPED TO THIS CALL: ``running_box_agent`` raises ``ConfigError`` too, for a
        # malformed STAMP, and "its settings did not resolve" would be false for that.
        # The stop still goes ahead either way.
        try:
            target, auth_src = box_writeback_context(std, proj, agent)
        except (SettingsError, ConfigError) as exc:
            detail = "" if any(str(prior) == str(exc) for prior in already_said) \
                else f"\n{exc}"
            print(
                f"Warning: credential writeback skipped for {container_name}: its "
                f"settings did not resolve.{detail}",
                file=sys.stderr,
            )
            return
        writeback_session_credentials(target, proj, auth_src=auth_src)
    except Exception:
        # Never let a writeback problem block the stop.
        pass


def _warn_settings(exc: KanibakoError) -> None:
    """A settings refusal, in its OWN words (P10), under an advisory prefix."""
    print(f"Warning: {exc}", file=sys.stderr)


def _load_paths(config: BootstrapConfig) -> tuple[StandardPaths, ConfigError | None]:
    """Standard paths, degrading a global SETTINGS file that will not parse.

    ⚑ The refusal is RETURNED, not printed; :func:`_stop_one` says it once.
    """
    try:
        return load_std_paths(config), None
    except ConfigError as exc:
        return load_std_paths(config, tolerate_bad_settings=True), exc


def _resolve_target(
    std: StandardPaths, config: BootstrapConfig, project_dir: str | None,
) -> tuple[ProjectPaths, ConfigError | None]:
    """The box resolve, degrading the ADVISORY read a malformed ``box.yaml`` kills.

    ⚑ The retry drops the three advisories (``warn=False``) — the nicety that
    could not be computed — and keeps the identity, which is what names the
    container.  A file the retry NEEDS still refuses and propagates.
    """
    try:
        return resolve_box_target(std, config, project_dir, initialize=False), None
    except ConfigError as exc:
        return resolve_box_target(
            std, config, project_dir, initialize=False, warn=False,
        ), exc


def _is_live(runtime: ContainerRuntime, name: str) -> bool:
    """Running, or PAUSED: podman reads a paused container as not running, yet it is live."""
    return runtime.is_running(name) or runtime.container_state(name) == "paused"


def _stop_one(runtime: ContainerRuntime, *, project_dir: str | None) -> int:
    """Stop the container for a single project."""
    config_file = user_config_file()
    config = load_config(config_file)
    std, paths_refusal = _load_paths(config)

    proj, box_refusal = _resolve_target(std, config, project_dir)

    # ⚑ SAID ONLY NOW, ONCE, BY TEXT: a refusal that re-raised out of its own
    # retry propagated instead, and one bad file can refuse both reads.
    said: list[ConfigError] = []
    for refusal in (paths_refusal, box_refusal):
        if refusal is None or any(str(prior) == str(refusal) for prior in said):
            continue
        said.append(refusal)
        _warn_settings(refusal)

    # Found by its labels (system-design § Box and helper identity), else its name.
    container_name = box_container(runtime, proj)
    # ⚑ NO legacy-name fallback: 1.8.0 is a clean break, no aliases.  So there is
    # nothing here to address — say so and name the cure (one carrier, in ``utils``).
    if container_name is None:
        print(
            unrenderable_box_name_refusal(
                proj.name or "", proj.mode.value, proj.project_path, project_dir,
                workset=container_name_segments(proj)[0], taken=box_name_taken(std, proj),
                connected=box_connected_workset(std, proj),
            ),
            file=sys.stderr,
        )
        return 1

    lock_file = proj.metadata_path / ".kanibako.lock"

    # ⚑⚑ ONE LIVENESS READING, TAKEN BEFORE ANYTHING CHANGES IT, SERVING BOTH
    # DECISIONS — the writeback below and the sentence printed further down.
    # A PAUSED box counts as live (``_is_live``): it is stopped and written back from.
    box_is_live = _is_live(runtime, container_name)

    # FIX 1: writeback BEFORE stopping — an in-box login must reach the host on
    # `kanibako stop` too.  Source the box's agent from its launch stamp
    # (KANIBAKO_AGENT) so we know which plugin's cred lifecycle to run; a box
    # launched before stamping (or a no-agent box) has no stamp -> skip.
    _writeback_on_stop(
        runtime, proj, container_name,
        std=std, config=config, box_is_live=box_is_live,
        already_said=tuple(said),
    )

    # ⚑ A PAUSED box is live: ``runtime.stop`` unpauses it, then stops it.
    if runtime.stop(container_name):
        # Clean up stopped container (persistent containers lack --rm)
        cleared = (not runtime.container_exists(container_name)
                   or runtime.rm(container_name))
        # 🛑 RC 0 IS NOT LIVENESS.  ``runtime.stop`` returns the runtime's exit
        # status, and ``podman stop`` exits 0 on a container that is ALREADY
        # EXITED — so this arm is reached both when a live box was really
        # stopped and when there was nothing running to stop.  Printing
        # "Stopped" for both told a user who had just been told the box was
        # not running that we had stopped it one command later.  The ACTION is
        # right either way — both arms reach ``container_exists`` -> ``rm``,
        # which is what clears the orphan and unblocks the next launch — so
        # only the SENTENCE branches, on the reading hoisted above.
        if box_is_live:
            print(f"Stopped {container_name}")
        elif cleared:
            print(f"Removed stopped container: {container_name}")
    else:
        print(f"No running container found for this project ({container_name})")
        # Clean up stopped persistent container if it exists
        if runtime.container_exists(container_name):
            cleared = runtime.rm(container_name)
            if cleared:
                print(f"Removed stopped container: {container_name}")
        else:
            cleared = True
            print(f"\n{stale_lock_cure(lock_file)}")

    # 🛑 A FAILED REMOVAL IS SAID AND FAILS THE RUN: the container still holds the name.
    if not cleared:
        sys.stdout.flush()
        print(f"Error: could not remove {container_name}; it still holds the box's name.",
              file=sys.stderr)
        return 1
    return 0


def _boxes_rendering_no_name() -> list[str]:
    """Names of registered boxes, in every mode, that render NO container name.

    ⚑ Enumerating REGISTRY boxes is what lets the sweep SAY it skipped one: such a box
    has no container, so it never appears in ``list_running``.  The walk is ``box
    list``'s: an unreadable workset is warned about there, a refused file here.

    ⚑ ONE SOURCE, ONE REFUSAL: the three mode reads each carry their OWN ``try``, so a
    registry that will not parse costs its OWN skip lines and no other mode's.
    """
    from kanibako.project.registry_store import load_standalone
    from kanibako.settings.paths import (BoxMode, _early_scope, iter_workset_projects,
                                         load_primary_boxes)
    from kanibako.utils import renders_no_name, workset_renders_no_name

    try:
        config = load_config(user_config_file())
        std, refusal = _load_paths(config)
    except (ConfigError, SettingsError, WorksetError, ProjectError) as exc:
        _warn_settings(exc)
        return []

    names: list[str] = []
    # A member of a LEGACY workset renders no name either, whatever its own name.
    unrendered: list[str] = []
    try:
        names.extend(load_primary_boxes(
            std.primary_workset, early=_early_scope(std, BoxMode.primary),
        ))
    except (ConfigError, SettingsError, WorksetError, ProjectError) as exc:
        _warn_settings(exc)

    try:
        names.extend(load_standalone(std.registry))
    except (ConfigError, SettingsError, WorksetError, ProjectError) as exc:
        _warn_settings(exc)

    try:
        for ws_name, _ws, members in iter_workset_projects(std, config):
            if workset_renders_no_name(ws_name):
                unrendered.extend(name for name, _status in members)
            else:
                names.extend(name for name, _status in members)
    except (ConfigError, SettingsError, WorksetError, ProjectError) as exc:
        _warn_settings(exc)

    if refusal is not None:
        _warn_settings(refusal)
    return [name for name in names if renders_no_name(name)] + unrendered


def _stop_all(runtime: ContainerRuntime, *, force: bool = False) -> int:
    """Stop and remove each running kanibako container; remove each stopped one.

    Containers are handled in listing order.  A stopped container still holds
    the box's name and blocks the next launch, so it is removed too.  Returns 1
    if any stop or removal, or the label query, failed, after handling every container.
    """
    # ⚑ SKIP AND CONTINUE: the sweep carries on with every container that has a name.
    for name in _boxes_rendering_no_name():
        print(f"Skipped box '{name}': it has no container name under the box-name rule.")

    names = [name for name, _image, _status in runtime.list_running(include_stopped=True)]
    # ⚑ AND every container carrying a box's labels, whatever its name (system-design
    # § Box and helper identity).  A query the runtime fails is said, and fails the run;
    # the containers found by name are still handled.
    unlabeled: LabelQueryError | None = None
    try:
        names += [n for n in runtime.find_labeled({LABEL_BOX: None}, check=True)
                  if n not in names]
    except LabelQueryError as exc:
        unlabeled = exc
        sys.stdout.flush()
        print(f"Error: could not list the containers that carry a box's labels: {exc}",
              file=sys.stderr)
    if not names:
        if unlabeled is not None:
            return 1
        print("No kanibako containers found.")
        return 0

    # ⚑ LIVENESS ONCE, BEFORE ANYTHING CHANGES IT — the prompt and the action read it.
    liveliness = {name: _is_live(runtime, name) for name in names}
    running_names = [n for n, live in liveliness.items() if live]
    stopped_names = [n for n, live in liveliness.items() if not live]

    if not force:
        print(f"This will stop {len(running_names)} running container(s) "
              f"and remove {len(stopped_names)} stopped container(s):")
        for n in running_names:
            print(f"  {n}  (running)")
        for n in stopped_names:
            print(f"  {n}  (stopped)")
        print()
        try:
            answer = input("Continue? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer not in ("y", "yes"):
            print("Aborted.")
            return 2

    failed = unlabeled is not None

    def fail(line: str) -> None:
        nonlocal failed
        failed = True
        sys.stdout.flush()
        print(line, file=sys.stderr)

    stopped_count = 0
    removed_count = 0
    for name in names:
        if liveliness[name]:
            if runtime.stop(name):
                print(f"Stopped {name}")
                if runtime.container_exists(name) and not runtime.rm(name):
                    fail(f"Failed to remove {name}")
                stopped_count += 1
            else:
                fail(f"Failed to stop {name}")
        else:
            # Reuse the sentence ``_stop_one`` prints for an already-stopped box.
            if runtime.rm(name):
                print(f"Removed stopped container: {name}")
                removed_count += 1
            else:
                fail(f"Failed to remove {name}")

    print(f"\nStopped {stopped_count} container(s); removed {removed_count} stopped container(s).")
    return 1 if failed else 0

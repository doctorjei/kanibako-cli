"""kanibako stop: stop running kanibako containers."""

from __future__ import annotations

import argparse
import sys

from kanibako.settings.config import user_config_file, load_config, BootstrapConfig
from kanibako.runtime.container import ContainerRuntime
from kanibako.errors import ConfigError, ContainerError
from kanibako.settings.paths import (
    ProjectPaths,
    StandardPaths,
    load_std_paths,
    resolve_box_target,
)
from kanibako.settings.settings_resolve import SettingsError
from kanibako.utils import container_name_for, unrenderable_box_name_refusal


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

    ⚑ ``box_is_live`` is the caller's SINGLE ``is_running`` reading (P10), not a
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
    agent = runtime.inspect_env(container_name, "KANIBAKO_AGENT")
    if not agent:
        return
    try:
        from kanibako.commands.start import (
            _resolve_box_auth_source,
            writeback_session_credentials,
        )
        from kanibako.settings.agent_config import agent_settings_path
        from kanibako.agent_ref import agent_address_node, harness_of
        from kanibako.targets import resolve_target
        # 🛑 CANONICALIZE, THEN DERIVE. The stamp is the OUTSIDE spelling (``+``) —
        # an env var is a place a human looks — but every use below is a KEY or a
        # key-derived lookup, so the value re-enters code as a node. ``harness_of``
        # splits on ``℘`` ALONE: given the raw ``navigator+claude`` it returns the
        # WHOLE string and ``resolve_target`` raises KeyError, which the blanket
        # catch below would swallow — writeback SILENTLY stopping for every persona
        # box. ⚑ Also the BACK-COMPAT seam: a box stamped ``℘`` by an older version
        # still works, because the ref grammar accepts both separators.
        # ⚑ THE ADDRESS GRAMMAR: a plain-shell box is stamped ``shell`` (its agent IS
        # the shell pseudo-agent, keyspec §2b). It resolves to ``ShellTarget``, whose
        # box-tier auth source makes the writeback below a clean no-op — where the
        # claimant grammar raised into the blanket catch.
        # 🛑 AND FOLD, through the one carrier: the stamp is a VALUE-supplied spelling,
        # so it folds at the hop that reaches for a node ([R173]) — otherwise a box whose
        # plugin declares a capital reads ``agents/Kirobo/agent.yaml``, a file the launch
        # never wrote, and the blanket catch below turns that into credential writeback
        # silently not happening.  Harness only; why: ``agent_address_node``.
        agent = agent_address_node(agent)
        # The target/plugin is keyed by the HARNESS; ``agent_name=agent`` below
        # keeps the node (keyspace slot).
        target = resolve_target(harness_of(agent), proj.project_path)
        # The cascade box/workset tier files are single-sourced mode-aware inside
        # the resolver (P6c) — standalone reads its file as the WORKSET tier.
        # ⚑ The §1A SELECTION LEVEL is REQUIRED (P7): ``meta.box.auth.workset_path``
        # resolves ``@workset.auth.path/@system.agent``, so without it the per-agent
        # credential dir names the agent the STORED settings select (the workset auth
        # ROOT when none is stored) and this writeback would land in that directory
        # while the LAUNCH delivered from ``<auth>/<agent>``.
        # The ``KANIBAKO_AGENT`` stamp IS the resolved selection for a running box.
        agent_cfg_path = agent_settings_path(std.agents, agent)
        # 🛑 NOT SILENT: a settings file that turned invalid after the box started (a
        # §0 undeclared key, a stray beside ``self:`` in ``agent.yaml``, a YAML syntax
        # error) refuses the auth resolve, and swallowing that left the user's
        # credentials un-written-back with no word said. The refusal's own text names
        # the file, so it is printed whole. ⚑ SCOPED TO THIS CALL: ``agent_address_node``
        # above raises ``ConfigError`` too, for a malformed STAMP, and "its settings did
        # not resolve" would be false for that. The stop still goes ahead either way.
        try:
            auth_src = _resolve_box_auth_source(
                std=std,
                proj=proj,
                agent_name=agent,
                system_settings_path=std.settings,
                agent_cfg_path=agent_cfg_path,
                selection_level={"system.agent": agent},
            )
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


def _warn_settings(exc: ConfigError) -> None:
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

    container_name = container_name_for(proj)
    # ⚑ NO legacy-name fallback: 1.8.0 is a clean break, no aliases.  So there is
    # nothing here to address — say so and name the cure (one carrier, in ``utils``).
    if container_name is None:
        print(
            unrenderable_box_name_refusal(
                proj.name or "", proj.mode.value, proj.project_path,
            ),
            file=sys.stderr,
        )
        return 1

    lock_file = proj.metadata_path / ".kanibako.lock"

    # ⚑⚑ ONE LIVENESS READING, TAKEN BEFORE ANYTHING CHANGES IT, SERVING BOTH
    # DECISIONS — the writeback below and the sentence printed further down.
    # Same shape and same call count as the launch guard in ``start.py``.
    box_is_live = runtime.is_running(container_name)

    # FIX 1: writeback BEFORE stopping — an in-box login must reach the host on
    # `kanibako stop` too.  Source the box's agent from its launch stamp
    # (KANIBAKO_AGENT) so we know which plugin's cred lifecycle to run; a box
    # launched before stamping (or a no-agent box) has no stamp -> skip.
    _writeback_on_stop(
        runtime, proj, container_name,
        std=std, config=config, box_is_live=box_is_live,
        already_said=tuple(said),
    )

    if runtime.stop(container_name):
        # Clean up stopped container (persistent containers lack --rm)
        if runtime.container_exists(container_name):
            runtime.rm(container_name)
        # 🛑 RC 0 IS NOT LIVENESS.  ``runtime.stop`` returns the runtime's exit
        # status, and ``podman stop`` exits 0 on a container that is ALREADY
        # EXITED — so this arm is reached both when a live box was really
        # stopped and when there was nothing running to stop.  Printing
        # "Stopped" for both told a user who had just been told the box was
        # not running that we had stopped it one command later.  The ACTION is
        # right either way — both arms reach ``container_exists`` -> ``rm``,
        # which is what clears the orphan and unblocks the next launch — so
        # only the SENTENCE branches, on the reading hoisted above.
        # ⚑ The removal sentence is safe to print unconditionally in this arm:
        # rc 0 means the runtime FOUND the container (podman and docker both
        # fail a stop on a name that is not there), so the ``rm`` above ran.
        if box_is_live:
            print(f"Stopped {container_name}")
        else:
            print(f"Removed stopped container: {container_name}")
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


def _boxes_rendering_no_name() -> list[str]:
    """Names of registered boxes, in every mode, that render NO container name.

    ⚑ Enumerating REGISTRY boxes is what lets the sweep SAY it skipped one: such a box
    has no container, so it never appears in ``list_running``.  The walk is ``box
    list``'s: an unreadable workset is warned about there, a refused file here.
    """
    from kanibako.project.registry_store import load_standalone
    from kanibako.settings.paths import (BoxMode, _early_scope, iter_workset_projects,
                                         load_primary_boxes)
    from kanibako.utils import renders_no_name

    config = load_config(user_config_file())
    try:
        std, refusal = _load_paths(config)
    except ConfigError as exc:
        _warn_settings(exc)
        return []
    if refusal is not None:
        _warn_settings(refusal)
    try:
        names = [*load_primary_boxes(std.primary_workset, early=_early_scope(std, BoxMode.primary)),
                 *load_standalone(std.registry)]
        for _ws_name, _ws, members in iter_workset_projects(std, config):
            names.extend(name for name, _status in members)
    except ConfigError as exc:
        _warn_settings(exc)
        return []
    return [name for name in names if renders_no_name(name)]


def _stop_all(runtime: ContainerRuntime, *, force: bool = False) -> int:
    """Stop all running kanibako containers."""
    # ⚑ SKIP AND CONTINUE: the sweep carries on with every container that has a name.
    for name in _boxes_rendering_no_name():
        print(f"Skipped box '{name}': it has no container name under the box-name rule.")

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

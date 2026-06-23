"""kanibako start / shell: container launch with credential flow."""

from __future__ import annotations

import argparse
import fcntl
import shutil
import subprocess
import sys
from pathlib import Path

from kanibako.agent_config import (
    agent_settings_path,
    load_agent_config,
    write_agent_config,
)
from kanibako.commands.diagnose import probe_missing_executables
from kanibako.config import (
    BOX_META_FILE,
    config_file_path,
    load_config,
    load_merged_config,
)
from kanibako.container import ContainerRuntime
from kanibako.errors import ContainerError, KanibakoError
from kanibako.log import get_logger
from kanibako.rig_registry import load_registry, registry_path
from kanibako.rig_resolve import resolve_rig
from kanibako.paths import (
    _upgrade_shell,
    box_state_home,
    xdg,
    load_std_paths,
    resolve_box_target,
)
from kanibako.targets import assembly, credsync, resolve_target
from kanibako.targets.assembly import BindingSourceError, descriptor_mounts
from kanibako.utils import container_name_for, project_hash, short_hash


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
        help="Use IMAGE as the container image for this run (--rig is the preferred spelling)",
    )
    p.add_argument(
        "--rig", dest="image", default=None,
        help="Rig (image) to use; synonym for --image",
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
        "project", nargs="?", default=None,
        help="Project directory or registered name (omit for current dir)",
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
        help="Use IMAGE as the container image for this run (--rig is the preferred spelling)",
    )
    p.add_argument(
        "--rig", dest="image", default=None,
        help="Rig (image) to use; synonym for --image",
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
        "project", nargs="?", default=None,
        help="Project directory or registered name (omit for current dir)",
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
        # Default: persistent when the configured bootstrap program is available
        persistent = _bootstrap_available(_resolve_bootstrap_program())
    env_vars = getattr(args, "env", None) or []
    # Reconcile the positional subject with the blanket --box flag (same → warn,
    # differ → error).  The winner is the path-or-name routed through
    # resolve_box_target in _run_container.
    from kanibako.commands.flags import resolve_subject_value
    project_dir = resolve_subject_value(
        getattr(args, "project", None), getattr(args, "box", None),
    )
    agent_args = getattr(args, "agent_args", [])

    # Map -A/-S to safe_mode: -A means autonomous (safe_mode=False),
    # -S means secure (safe_mode=True). Neither means autonomous (default).
    safe_mode = secure
    autonomous = getattr(args, "autonomous", False)

    # Agent resolution happens UP FRONT inside _run_container via the unified
    # resolve_agent cascade (explicit > box > workset > system default → the
    # installed-count rule).  Nothing-resolved on this agent-requiring command
    # raises a typed AgentResolutionError (Gate-2a/2b) which the top-level
    # cli.py handler surfaces verbatim with a non-zero exit — NEVER a silent
    # drop to shell.  `kanibako shell` (run_shell) bypasses this entirely.
    explicit_agent = getattr(args, "agent", None)  # Phase D seam (--agent flag)

    return _run_container(
        project_dir=project_dir,
        entrypoint=entrypoint,
        image_override=image_override,
        new_session=new_session,
        safe_mode=safe_mode,
        autonomous=autonomous,
        resume_mode=resume_session,
        extra_args=agent_args,
        no_helpers=no_helpers,
        no_auto_auth=no_auto_auth,
        browser=browser,
        share_images=share_images,
        persistent=persistent,
        model_override=model_override,
        cli_env=env_vars,
        explicit_agent=explicit_agent,
    )


def run_shell(args: argparse.Namespace) -> int:
    from kanibako.commands.flags import resolve_subject_value
    project_dir = resolve_subject_value(
        getattr(args, "project", None), getattr(args, "box", None),
    )
    shell_args = getattr(args, "shell_args", [])

    entrypoint = getattr(args, "entrypoint", None)
    box_shell_mode = False
    if not entrypoint:
        if shell_args:
            # One-off command exec: /bin/sh -c "<cmd>" (not the interactive shell).
            entrypoint = "/bin/sh"
        else:
            # Interactive shell: defer to _run_container's image-aware box.shell
            # resolution.  We leave entrypoint=None and flag box_shell_mode so
            # _run_container resolves the shell *with* the runtime/image handle
            # (box.shell -> $KANIBAKO_SHELL -> stored image login shell -> sh)
            # without engaging an agent.
            box_shell_mode = True
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
        autonomous=False,
        resume_mode=False,
        extra_args=shell_args,
        no_helpers=no_helpers,
        share_images=share_images,
        persistent=persistent,
        cli_env=env_vars,
        box_shell_mode=box_shell_mode,
    )


def _resolve_bootstrap_program() -> str:
    """Resolve the configured bootstrap program for the host-side default-mode
    heuristic (machine + user global, no project).

    The authoritative per-launch value is read from the fully-merged config in
    ``_run_container`` (which includes workset/project/CLI overrides); this is
    only the cheap pre-resolution used to pick the default persistence mode.
    """
    try:
        cfg_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
        return load_merged_config(cfg_file, None).box_bootstrap_program
    except Exception:
        return "tmux"


def _bootstrap_available(program: str = "tmux") -> bool:
    """Check if the host-side bootstrap program is installed.

    Used to decide the default persistence mode (persistent only when the
    bootstrap program is present on the host, since reattach shells out to it).
    """
    return shutil.which(program) is not None


# Sentinel returned by _check_launch_baseline when the launch-critical bootstrap
# program is missing from the image (tier-1 hard-stop).
_BOOTSTRAP_MISSING = object()


def _launch_issues_path(std, container_name: str) -> Path:
    """State-file path for a box's tier-2 launch warnings.

    Uses XDG_STATE (``$XDG_STATE_HOME/kanibako/launch-issues.<box>``) so the
    warnings survive the bootstrap session and can be reprinted on exit.
    """
    state_home = xdg("XDG_STATE_HOME", ".local/state")
    return state_home / "kanibako" / f"launch-issues.{container_name}"


def _check_launch_baseline(runtime, image, bootstrap_program, container_name, std):
    """Run the two-tier baseline probe against *image* before launch.

    Performs ONE ephemeral probe covering the bootstrap program (tier 1) plus
    every baseline executable (tier 2).

    * Tier 1 — if the bootstrap program is missing, prints a hard-stop message
      (noting a shell is still reachable to investigate) and returns
      :data:`_BOOTSTRAP_MISSING`.
    * Tier 2 — returns the list of ``(package, executable)`` pairs whose
      executable is missing.  These are WARN-only: they are persisted to the
      box's launch-issues state file and surfaced after the session closes.
    """
    from kanibako import baseline as baseline_mod

    pairs = baseline_mod.executables()  # [(pkg, exe), ...]
    baseline_exes = [exe for _pkg, exe in pairs]
    exe_to_pkg = {exe: pkg for pkg, exe in pairs}

    # One probe for bootstrap + all baseline exes (dedup, bootstrap first).
    probe_exes: list[str] = [bootstrap_program]
    for exe in baseline_exes:
        if exe not in probe_exes:
            probe_exes.append(exe)
    missing = set(probe_missing_executables(runtime, image, probe_exes))

    # TIER 1: bootstrap program.
    if bootstrap_program in missing:
        print(
            f"Error: the bootstrap program '{bootstrap_program}' is not "
            f"installed in image '{image}'.\n"
            f"  Kanibako cannot start the interactive session without it.\n"
            f"  A shell IS still available to investigate, e.g.:\n"
            f"      {runtime.cmd} run --rm -it {image} bash\n"
            f"  or, once a box exists:  kanibako shell\n"
            f"  Install it in the image or set 'box.bootstrap_program' to an "
            f"installed program.",
            file=sys.stderr,
        )
        return _BOOTSTRAP_MISSING

    # TIER 2: rest of the baseline (warn only).
    tier2 = [
        (exe_to_pkg.get(exe, "?"), exe)
        for exe in baseline_exes
        if exe in missing
    ]

    issues_path = _launch_issues_path(std, container_name)
    try:
        if tier2:
            issues_path.parent.mkdir(parents=True, exist_ok=True)
            issues_path.write_text(
                "\n".join(f"{pkg}: {exe}" for pkg, exe in tier2) + "\n"
            )
            # Print before launch too (bonus; the alt-screen wipes it, hence we
            # also reprint after the session closes).
            print(
                "Warning: the image is missing baseline tools — the session "
                "will still launch:",
                file=sys.stderr,
            )
            for pkg, exe in tier2:
                print(f"  - {pkg}: '{exe}'", file=sys.stderr)
        else:
            # Clear any stale issues from a previous launch.
            issues_path.unlink(missing_ok=True)
    except OSError:
        pass  # best-effort; never block launch on the state file.

    return tier2


def _print_launch_issues(std, container_name: str) -> None:
    """Reprint a box's persisted tier-2 launch warnings (post-session)."""
    issues_path = _launch_issues_path(std, container_name)
    try:
        text = issues_path.read_text().strip()
    except OSError:
        return
    if not text:
        return
    print(
        "\nNote: this box's image is missing baseline tools "
        f"(from {issues_path}):",
        file=sys.stderr,
    )
    for line in text.splitlines():
        print(f"  - {line}", file=sys.stderr)
    print(
        "  Run 'kanibako rig diagnose' to recheck, or rebuild/update the image.",
        file=sys.stderr,
    )


def _bootstrap_wrap(program: str, inner_cmd: str, cli_args: list[str]) -> tuple[str, list[str]]:
    """Build the (entrypoint, args) that launches *inner_cmd* under *program*.

    For tmux (the default) this preserves the persistent-session shape
    ``tmux new-session -s kanibako -- <inner_cmd> <cli_args...>``.  For any other
    bootstrap program the contract is intentionally MINIMAL/best-effort: the
    program is exec'd with the inner command and its args appended
    (``<program> <inner_cmd> <cli_args...>``); a non-tmux program is responsible
    for whatever session/multiplexing semantics it wants.
    """
    if program == "tmux":
        args = ["new-session", "-s", "kanibako", "--", inner_cmd, *cli_args]
        return "tmux", args
    return program, [inner_cmd, *cli_args]


def _bootstrap_attach(program: str) -> list[str]:
    """Build the in-container command that re-attaches to the bootstrap session.

    tmux uses ``tmux attach -t kanibako``; for a non-tmux bootstrap (which has
    no kanibako-named session contract) we fall back to exec'ing the program
    bare (best-effort) — non-tmux reattach is not a guaranteed feature.
    """
    if program == "tmux":
        return ["tmux", "attach", "-t", "kanibako"]
    return [program]


def _tmux_session_name(project_name: str) -> str:
    """Generate a deterministic tmux session name for host-side reattach."""
    return f"kanibako-{project_name}"


def _tmux_has_session(session_name: str) -> bool:
    """Check if a tmux session exists on the host."""
    return subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        capture_output=True,
    ).returncode == 0


def _apply_tweakcc(install, agent_cfg, cache_path, image, runtime_cmd, logger):
    """Apply tweakcc patching if enabled in agent config.

    Patching runs inside a throwaway container (``podman run --rm``) using
    the same image that will be used for the agent.  The patched binary is
    cached on disk with flock-based reference counting.

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
            # Write merged config to cache dir (will be mounted into container)
            config_file = cache_dir / f".config-{key}.json"
            write_merged_config(merged_config, config_file)

            def patch_fn(staging_dir, staging_binary):
                """Run tweakcc --apply inside a throwaway container."""
                cmd = [
                    runtime_cmd, "run", "--rm", "--network=none",
                    "-v", f"{staging_dir}:/work:rw",
                    "-v", f"{config_file}:/root/.tweakcc/config.json:ro",
                    "-e", f"TWEAKCC_CC_INSTALLATION_PATH=/work/{staging_binary.name}",
                    image,
                    "tweakcc", "--apply",
                ]
                logger.debug("Running tweakcc via container: %s", cmd)
                result = subprocess.run(
                    cmd, capture_output=True, text=True, check=False,
                )
                if result.returncode != 0:
                    raise TweakccCacheError(
                        f"tweakcc container failed (exit {result.returncode}): "
                        f"{result.stderr.strip()}"
                    )

            entry = cache.put(key, install.binary, patch_fn)
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


def _parse_cli_env(cli_env: list[str] | None) -> dict[str, str]:
    """Parse ``-e/--env KEY=VALUE`` items into a dict (ignores malformed ones)."""
    env: dict[str, str] = {}
    for item in cli_env or []:
        if "=" in item:
            k, v = item.split("=", 1)
            env[k] = v
    return env


def _run_container(
    *,
    project_dir: str | None,
    entrypoint: str | None,
    image_override: str | None,
    new_session: bool,
    safe_mode: bool,
    autonomous: bool = False,
    resume_mode: bool,
    extra_args: list[str],
    no_helpers: bool = False,
    no_auto_auth: bool = False,
    browser: bool = False,
    share_images: bool = False,
    persistent: bool = False,
    model_override: str | None = None,
    cli_env: list[str] | None = None,
    box_shell_mode: bool = False,
    explicit_agent: str | None = None,
    setup_only: bool = False,
    _is_retry: bool = False,
) -> int:
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)

    std = load_std_paths(config)
    # SYSTEM tier of the SETTINGS cascade = @system.settings = global/settings.yaml
    # (behavior keys), distinct from the kanibako.yaml CONFIG file (system.* layout).
    system_settings_path = std.settings

    # project_dir is the reconciled subject (positional OR --box) computed in
    # run_start/run_shell.  Route it through the path-or-name resolver so a bare
    # registered box name selects that box even when not cwd (§Design 8).
    proj = resolve_box_target(std, config, project_dir, initialize=True)

    # Hint about orphaned project data when initializing a new project
    if proj.is_new and proj.group is not None and proj.group.is_default:
        from kanibako.paths import iter_projects
        for _settings, _ppath in iter_projects(std, config):
            if _ppath is not None and not _ppath.is_dir():
                print(
                    "hint: orphaned project data detected — "
                    "run 'kanibako box list' or use 'kanibako box remap' "
                    "if you moved a project.",
                    file=sys.stderr,
                )
                break

    # Load merged config (global + workset + project)
    project_toml = proj.metadata_path / BOX_META_FILE
    workset_path = (proj.group.root / "settings.yaml") if proj.group is not None else None
    merged = load_merged_config(
        config_file,
        project_toml,
        workset_path=workset_path,
        cli_overrides={"box_image": image_override} if image_override else None,
    )

    image = merged.box_image
    bootstrap_program = merged.box_bootstrap_program or "tmux"

    # Persist image override for new projects so it becomes the default
    if proj.is_new and image_override:
        from kanibako.config import write_project_config
        write_project_config(project_toml, image_override)

    # Resolve target (agent plugin) and detect installation.
    #
    # W1 §Design 7: "resolve config FIRST ... before anything else."  Agent
    # resolution depends only on config/settings (merged config + system
    # default), NOT on the image being pulled or the bootstrap baseline.  So
    # resolve it up front — BEFORE ensure_image (image pull) and the tmux
    # baseline check below — so a user with 2+ agents and no default hits the
    # Gate-2a "pick an agent" error immediately, rather than paying a full
    # image pull and then a tmux baseline error.
    #
    # `kanibako shell` (box_shell_mode) and explicit-entrypoint launches skip
    # resolution entirely (they need no agent); this is unchanged.
    logger = get_logger("start")

    # Detect the container runtime up front: agent resolution below needs it to
    # honour a REATTACH to an already-running persistent box (the box's stored
    # agent supersedes the cascade), and the image step further down needs it
    # too.  Detection is cheap and side-effect-free.
    try:
        runtime = ContainerRuntime()
    except ContainerError:
        print(
            "Error: No container runtime found.\n"
            "Install podman (https://podman.io/) or Docker, then try again.",
            file=sys.stderr,
        )
        return 1

    # Reattach fast-source: for a PERSISTENT box that is ALREADY RUNNING, the
    # box's identity is its container name (agent-independent) and `kanibako
    # start` should simply reattach.  The reattach path needs an agent only for
    # the per-agent credential refresh below.  W1's resolve_agent would Gate-2a
    # ("pick an agent") when 2+ agents exist with no default — even though the
    # box is happily running with a known agent.  So source that agent from the
    # container's KANIBAKO_AGENT stamp (set at launch) and feed it into the
    # cascade as the explicit choice.  The RUNNING BOX WINS over a differing
    # system default (silently); a differing EXPLICIT --agent is a hard error
    # (stop the box to relaunch with a different agent).  Boxes launched before
    # this change have no stamp -> inspect_env returns None -> normal resolution
    # (a default/--agent is then required, unchanged behaviour).
    reattach_running = False
    stored_agent: str | None = None
    if persistent and runtime.is_running(container_name_for(proj)):
        reattach_running = True
        stored_agent = runtime.inspect_env(
            container_name_for(proj), "KANIBAKO_AGENT"
        )
        if stored_agent:
            if explicit_agent is not None and explicit_agent != stored_agent:
                raise KanibakoError(
                    f"Box '{proj.name}' is already running agent "
                    f"'{stored_agent}'; cannot reattach with --agent "
                    f"'{explicit_agent}'. Stop it first "
                    f"(`kanibako stop {proj.name}`) to relaunch with a "
                    f"different agent."
                )
            explicit_agent = stored_agent

    is_agent_mode = entrypoint is None and not box_shell_mode
    target = None
    install = None
    if is_agent_mode:
        from kanibako.config import resolve_agent
        # Resolve the agent via the full cascade (explicit > box > workset >
        # system default), then the installed-count rule.  workset_agent=None:
        # merged.box_agent already folds the workset tier (load_merged_config
        # overlays workset then box).  system.default_agent is a SETTING read
        # from the system settings file.  resolve_agent raises typed
        # AgentResolutionError subclasses (Gate-2a/2b / adapter-missing) which
        # the top-level cli.py handler surfaces verbatim with a non-zero exit.
        agent_name = resolve_agent(
            explicit_agent=explicit_agent,
            box_agent=merged.box_agent,
            workset_agent=None,
            system_default_path=system_settings_path,
            project_path=proj.project_path,
        )
        target = resolve_target(agent_name, proj.project_path)
        logger.debug("Resolved target: %s", target.display_name)
        # First detect: early-out / "is the agent present on the host". The
        # "Using host ...:" line is deferred until after prepare_host() (the
        # update gate) so it names the real, post-update version — see the
        # re-detect below.
        install = target.detect()
        if not install and target.has_binary:
            print(
                f"Warning: {target.display_name} binary not found on host. "
                f"Launching without agent.",
                file=sys.stderr,
            )
            logger.debug("target.detect() returned None for %s", target.name)

    # Resolve the rig name to a kind + prep action, then materialize it.
    # Templates BUILD their Containerfile; prefabs/bases are pull-only via
    # ensure_image (inspect -> pull; no local base build).
    containers_dir = std.data_path / "containers"
    registry = load_registry(registry_path(std))
    res = resolve_rig(image, runtime, std, merged, registry=registry)
    try:
        if (
            res.kind == "template"
            and res.containerfile is not None
            and not runtime.image_exists(res.image)
        ):
            print(
                f"Rig '{image}' isn't prepped — building...",
                file=sys.stderr,
            )
            rc = runtime.rebuild(
                res.image,
                res.containerfile,
                res.containerfile.parent,
                build_args=None,
            )
            if rc != 0:
                print(
                    f"Error: failed to build rig '{image}' "
                    f"(exit code {rc}).",
                    file=sys.stderr,
                )
                return 1
        else:
            # Prefab/base (or already-local template/extended): inspect, then
            # pull if missing. Base images are pull-only (no local build).
            runtime.ensure_image(res.image, containers_dir)
    except ContainerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    image = res.image

    # Capture the image's login shell (idempotent, never fatal) so the
    # box-shell resolver reads a stored value instead of probing in the hot path.
    from kanibako.shells import capture_image_shell
    capture_image_shell(runtime, image, std)

    # `kanibako shell` (interactive, no agent): resolve the box.shell now, with
    # the runtime/image handle, so the image-default tier participates.  This
    # makes entrypoint concrete *before* the agent-vs-shell decision (line ~672)
    # and the exec-into-running check (line ~737), so neither regresses.
    if box_shell_mode and entrypoint is None:
        from kanibako.shells import resolve_box_shell
        entrypoint, _src = resolve_box_shell(merged, std, runtime=runtime, image=image)

    from kanibako.freshness import check_image_freshness
    check_image_freshness(runtime, image, std.cache_path)

    # Two-tier launch verification (one ephemeral probe covers both tiers).
    # Only meaningful for a persistent, bootstrap-wrapped session: the bootstrap
    # program is launch-critical only when we actually run it. Ephemeral and
    # one-shot launches don't use it, so skip the probe (and its hard stop)
    # entirely — otherwise a minimal --image (no tmux) couldn't run ephemerally.
    #   TIER 1 (bootstrap) — missing → HARD STOP before launch.
    #   TIER 2 (rest of baseline) — missing → WARN only; persisted + surfaced
    #   after the bootstrap session closes.
    if persistent:
        if _check_launch_baseline(
            runtime, image, bootstrap_program, container_name_for(proj), std,
        ) is _BOOTSTRAP_MISSING:
            return 1

    # Load agent config
    agent_id = target.name if target else "general"
    agent_cfg_path = agent_settings_path(std.agents, agent_id)
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

    # Plugin descriptor (None for legacy/no_agent targets).  Hoisted here so the
    # credential lifecycle sites below (init / refresh / writeback) and the
    # launch-assembly block all branch off a single value: descriptor-bearing
    # targets route their cred lifecycle through the credsync engine, legacy
    # targets keep the per-plugin refresh/writeback hooks.
    desc = target.descriptor if target else None

    # Persistent mode: reattach if already running, clean up stale containers
    if persistent:
        if runtime.is_running(container_name):
            # Heads-up to STDERR (never stdout — must not pollute the tmux/agent
            # stream we're about to attach to).
            agent_label = target.name if target else (
                stored_agent if reattach_running and stored_agent else "shell"
            )
            print(
                f"Reattaching to running box '{proj.name}' "
                f"(agent: {agent_label}).",
                file=sys.stderr,
            )
            # Refresh credentials before reattaching
            if target and proj.group_auth:
                if desc is not None:
                    credsync.refresh_cred_files(
                        desc, target, host_home=Path.home(),
                        project_home=proj.shell_path, group_auth=proj.group_auth,
                    )
                else:
                    target.refresh_credentials(proj.shell_path)
            reattach_rc = runtime.exec(
                container_name, _bootstrap_attach(bootstrap_program)
            )
            # FIX 1: the reattach session has ended (detach or in-box exit).
            # An in-box login during this attach must reach the host, so write
            # back here too — the reattach path (4a32871) previously skipped the
            # post-session cred lifecycle entirely.
            writeback_session_credentials(target, proj)
            return reattach_rc
        # Stale stopped container: remove before recreating
        if runtime.container_exists(container_name):
            runtime.rm(container_name)
        # Persistent mode forces no helpers
        no_helpers = True
    else:
        # Interactive (shell/ephemeral) mode: if a container is already
        # running for this project AND we're in shell mode (entrypoint set,
        # no agent), exec into it instead of erroring — matches the natural
        # UX of `kanibako shell <name> -- cmd` against a live container.
        if runtime.is_running(container_name) and entrypoint is not None:
            exec_cmd = [entrypoint] + (extra_args or [])
            # Apply per-run -e/--env vars to the exec'd process. The container's
            # baseline env (env files, agent_cfg.env, KANIBAKO_NAME) was set at
            # launch and is inherited by exec; without this, per-run -e vars
            # would be silently dropped when the box is already running.
            return runtime.exec(
                container_name, exec_cmd, env=_parse_cli_env(cli_env)
            )
        if runtime.container_exists(container_name):
            print(
                "Error: A container already exists for this project.\n"
                "  Reattach:  kanibako start\n"
                "  Stop it:   kanibako stop",
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
                "Error: Another Kanibako session is already running for this project.\n"
                "  Stop it first:  kanibako stop\n"
                "  Or use a shell: kanibako shell",
                file=sys.stderr,
            )
            lock_fd.close()
            return 1

        # Record container name so `kanibako stop` can find it
        lock_fd.write(container_name + "\n")
        lock_fd.flush()

    try:
        # Auto-snapshot vault share-rw before launch.
        if proj.enable_vault and proj.vault_rw_path.is_dir():
            from kanibako.snapshots import auto_snapshot, detect_snapshot_strategy
            strategy = detect_snapshot_strategy(proj.vault_rw_path)
            snap = auto_snapshot(proj.vault_rw_path, strategy=strategy)
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

        # Seed-once gate (BUG-D): seed the box home exactly once, on its FIRST
        # start — whether the box was created by this very `start` (proj.is_new)
        # or earlier by `box create` (which makes the metadata dir, leaving
        # is_new False at start time).  The `.seeded` sentinel under the metadata
        # dir records completion so subsequent launches never re-seed (user edits
        # to seeded files survive, D-B6).
        from kanibako.templates import mark_seeded, needs_seed
        seed_box = needs_seed(proj.metadata_path, is_new=proj.is_new)

        # Template application + agent init for new projects.
        if seed_box:
            # Layered seed-once: copy the ordered template layers
            # (base -> agent -> workset; later overlays earlier, per-file
            # last-wins) into the box home ONCE at creation.  The base layer is
            # always present; the agent layer applies iff an agent target is
            # bound; the workset layer is None for STANDALONE boxes (skipped).
            from kanibako.templates import (
                agent_template_dir,
                apply_template_layers,
                base_template_dir,
                workset_template_dir,
            )
            layers: list[Path | None] = [base_template_dir(std)]
            if target:
                layers.append(agent_template_dir(std, target.name))
            layers.append(workset_template_dir(proj, std))
            apply_template_layers(proj.shell_path, layers)
        # Descriptor-bearing targets seed creds via the credsync engine.  A
        # descriptor-less target (only no_agent) has nothing to seed at init —
        # its dirs come from the layered template apply above — so there is no
        # else branch (the vestigial init_home hook was removed in 1.6.0).
        if seed_box and target and desc is not None:
            credsync.seed_cred_files(
                desc, target, host_home=Path.home(),
                project_home=proj.shell_path, group_auth=proj.group_auth,
            )

        # Copy-once-at-init seeds (additive; overlays templates). target may be
        # None (no agent) — seeds can still come from config levels.
        if seed_box:
            _apply_init_seeds(
                std=std, proj=proj, agent_name=agent_id, target=target,
                global_config_path=system_settings_path, project_toml=project_toml,
                workset_config_path=workset_path, agent_config_path=agent_cfg_path,
                logger=logger, group_auth=proj.group_auth,
            )

        # Record seed-once completion so subsequent launches skip the blocks
        # above (idempotent; only the first start writes meaningful content).
        if seed_box:
            mark_seeded(proj.metadata_path)

        # Synced copies (the `<scope>.synced.<name>` category) — applied on
        # EVERY launch (mtime-gated), unlike copy-once seeds.  Distinct from the
        # plugin descriptor's `cred_files` credsync engine above (that is
        # descriptor-driven; this is settings-driven), so there is no double
        # application.  ADDITIVE: with no `synced.*` keys configured the
        # reconciled copy set has no synced winners -> no-op.  The group_auth
        # gate (D-M4) suppresses every synced entry when group_auth is False.
        _apply_synced_copies(
            std=std, proj=proj, agent_name=agent_id, target=target,
            global_config_path=system_settings_path, project_toml=project_toml,
            workset_config_path=workset_path, agent_config_path=agent_cfg_path,
            logger=logger, group_auth=proj.group_auth,
        )

        # Plugin-owned pre-launch host preparation (agent-agnostic call).
        # The plugin owns everything agent-specific that must touch the host
        # before mounts: e.g. the Claude plugin runs a synchronous `claude
        # update` gate so the host binary/symlink are stable before we bind
        # them, then refreshes host auth with the auto-updater disabled.  This
        # runs BEFORE the binary validation below because the update step can
        # repoint the resolved binary.  The hook never raises.
        if target and install and is_agent_mode:
            target.prepare_host(
                install,
                auto_auth=bool(proj.group_auth and not no_auto_auth),
                data_path=std.data_path,
            )
            # Re-detect after the update gate.  prepare_host() can repoint /
            # prune the host version (the synchronous `claude update`), so the
            # first `install` (frozen before that) is stale.  Re-detecting here
            # — agent-agnostic — yields ONE fresh install that validate, the
            # "Using host ...:" print, and binary_mounts all consume, so they
            # describe and bind the real post-update version.  With the
            # auto-updater disabled the version is then stable detect→mount.
            install = target.detect() or install

        # Announce the (post-update) host agent now that prepare_host has run.
        if target and install and is_agent_mode:
            print(
                f"Using host {target.display_name}: {install.binary}",
                file=sys.stderr,
            )

        # Validate the resolved HOST binary BEFORE anything execs it.  A 0-byte
        # or non-executable file passes binary_mounts()' is_file() check and
        # would be exec'd into a brick.  This MUST run before the auth check
        # below: target.check_auth() shells out to the host agent binary, so a
        # corrupt/empty binary there raises an uncaught OSError (Exec format
        # error) and the user sees a Python traceback instead of the actionable
        # message.  Scoped to the real-agent path (entrypoint is None and not a
        # plain box shell launch).
        if target and install and is_agent_mode:
            from kanibako.targets.base import _validate_agent_binary

            reason = _validate_agent_binary(install.binary)
            if reason:
                print(
                    f"Error: {target.display_name} host binary is unusable: "
                    f"{reason}.\n"
                    f"  binary: {install.binary}\n"
                    f"The host agent binary appears corrupt or empty; the "
                    f"container would launch into a non-executable file.\n"
                    f"Reinstall the host {target.display_name} (and prune any "
                    f"stale 0-byte versions in {install.install_dir}), then "
                    f"retry.\n"
                    f"Run 'kanibako system diagnose' for a full health check.",
                    file=sys.stderr,
                )
                return 1

        # Pre-launch auth check (skip for distinct auth — creds live in project).
        #
        # On failure: if the target declares an in-box setup command
        # (``setup_entrypoint`` — goose ``configure`` / codex ``login``), DEFER the
        # error and run that command interactively IN THE BOX just before launch
        # (the box must be assembled first — image/mounts/env are built below).  An
        # agent with no setup command (claude, by default) errors out here as
        # before.  ``needs_inbox_setup`` carries the decision to the launch block.
        needs_inbox_setup = False
        if target and install and proj.group_auth:
            if not target.check_auth():
                if target.setup_entrypoint is not None:
                    needs_inbox_setup = True
                else:
                    print(
                        "Error: Authentication failed.\n"
                        "  Re-authenticate:  kanibako agent reauth\n"
                        "  Skip agent:       kanibako shell",
                        file=sys.stderr,
                    )
                    return 1

        # Credential refresh via target (skip for distinct auth)
        if target and proj.group_auth:
            if desc is not None:
                credsync.refresh_cred_files(
                    desc, target, host_home=Path.home(),
                    project_home=proj.shell_path, group_auth=proj.group_auth,
                )
            else:
                target.refresh_credentials(proj.shell_path)

        # tweakcc: patch agent binary if enabled
        tweakcc_entry = None
        tweakcc_cache_obj = None
        if target and install and agent_cfg.tweakcc:
            result = _apply_tweakcc(
                install, agent_cfg, std.cache_path, image, runtime.cmd, logger,
            )
            if result:
                install, tweakcc_entry, tweakcc_cache_obj = result

        # Build CLI args via target, merging agent run_args and state
        if target:
            effective_state = _build_effective_state(
                target,
                agent_cfg,
                project_toml,
                global_config_path=system_settings_path,
                workset_config_path=workset_path,
            )
            # Apply model override from -M/--model flag
            if model_override:
                effective_state["model"] = model_override
            all_extra = list(agent_cfg.run_args) + list(extra_args)
            if desc is not None:
                # Descriptor path: assemble argv + container-env overlay
                # declaratively from the plugin descriptor (replaces the legacy
                # apply_state / build_cli_args hooks for descriptor-bearing
                # targets).
                #
                # safe_off redeems the persisted `access` setting (claude:
                # safe_bypass.setting_key="access", default "permissive") while
                # the per-launch -A/-S flags still win (safe_mode IS the -S
                # `secure` bool; autonomous IS -A).  An agent whose descriptor
                # declares no safe_bypass.setting_key (goose/codex) falls back to
                # default-autonomous via effective_safe_mode_off.
                sb = desc.safe_bypass
                persisted_access = (
                    effective_state.get(sb.setting_key, "")
                    if sb is not None and sb.setting_key
                    else ""
                )
                safe_off = assembly.effective_safe_mode_off(
                    secure=safe_mode,
                    autonomous=autonomous,
                    persisted_access=persisted_access,
                )
                mode_key = assembly.resolve_mode(
                    resume_mode=resume_mode,
                    new_session=new_session,
                    is_new_project=proj.is_new,
                    extra_args=all_extra,
                    available_modes=desc.mode.keys(),
                )
                cli_args = assembly.assemble_argv(
                    desc,
                    mode_key=mode_key,
                    safe_mode_off=safe_off,
                    setting_values=effective_state,
                    op=None,
                    extra_args=all_extra,
                )
                state_env = assembly.assemble_env(
                    desc,
                    safe_mode_off=safe_off,
                    setting_values=effective_state,
                )
            else:
                # Descriptor-less target: the only one is NoAgentTarget (the
                # `kanibako shell` fallback), which launches a plain shell with
                # no agent argv and no state env.  The legacy build_cli_args /
                # apply_state hook dispatch was removed for the public release
                # (descriptor-only plugin system); a no-agent box needs neither.
                cli_args = []
                state_env = {}
        else:
            state_env = {}
            cli_args = list(extra_args)

        # Build extra mounts from target binary detection
        extra_mounts = []
        if target and install:
            # NOTE: the resolved HOST binary is validated earlier (before the
            # auth check) via _validate_agent_binary, so a 0-byte /
            # non-executable file fails fast with an actionable message before
            # anything execs it.  Here we only guard against missing mount
            # sources.
            if desc is not None:
                # Descriptor path: the AGENT_CRITICAL delivery binds (binary +
                # launcher) come from the descriptor.  A missing/unresolvable
                # source raises BindingSourceError -> clean safe-fail (replaces
                # the legacy "mount source disappeared" check), not a crun crash.
                #
                # Agent-scope shared dirs (e.g. claude's plugins/cache) are no
                # longer descriptor bindings; they flow through the unified
                # category resolver (``agent.shared.*`` from the plugin's
                # ``default_shares()``, rooted at ``@system.agents/<agent>``) and
                # are emitted by ``_build_share_mounts`` below — host-side dirs
                # guarantee-created there (L7).
                #
                # Per-agent binding host-source overrides (agent.<name>.binding.<key>
                # layered over agent.default.binding) resolved across the config
                # cascade; an override redirects (and always wins for) a binding's
                # host source.
                binding_overrides = _build_binding_overrides(
                    project_toml=project_toml,
                    workset_config_path=workset_path,
                    agent_config_path=agent_cfg_path,
                    global_config_path=system_settings_path,
                    agent_name=agent_id,
                )
                try:
                    binary_mnts = descriptor_mounts(
                        desc, install,
                        overrides=binding_overrides,
                    )
                except BindingSourceError as exc:
                    logger.error("Agent delivery binding unusable: %s", exc)
                    print(
                        f"Error: {target.display_name} mount source "
                        f"disappeared before launch: {exc}\n"
                        f"The host agent install changed while starting (e.g. "
                        f"an update pruned a version). Retry the launch.\n"
                        f"Run 'kanibako system diagnose' for a full health "
                        f"check.",
                        file=sys.stderr,
                    )
                    return 1
                extra_mounts.extend(binary_mnts)
            # No descriptor-less branch: every target with a host `install` is
            # descriptor-bearing (all shipped plugins), so its delivery binds
            # come from descriptor_mounts above.  NoAgentTarget has no binary
            # (install is None), so it never reaches this block at all.

        # kanibako CLI bind-mount (package + entry script)
        kanibako_mnts = _kanibako_mounts()
        extra_mounts.extend(kanibako_mnts)

        # Scoped bindings (settings-framework {scope}.bindings.{ro,rw}.*).
        # Additive: empty config → no mounts → no behavior change.
        share_mounts = _build_share_mounts(
            std=std,
            proj=proj,
            agent_name=agent_id,
            global_config_path=system_settings_path,
            project_toml=project_toml,
            workset_config_path=workset_path,
            agent_config_path=agent_cfg_path,
            target=target,
            group_auth=proj.group_auth,
        )
        extra_mounts.extend(share_mounts)

        # Masks (decision B): resolve the ``box.masks`` tmpfs mask LIST through
        # the category model.  The vault mask (~/workspace/vault) is injected
        # unconditionally (every box mode); a box may add masks or suppress all
        # via a terminal "" on box.masks.  The result drives
        # runtime.run(tmpfs_masks=...) below — generalizing the old hardcoded,
        # single, default-workset-only flag.
        tmpfs_masks = _resolve_masks(
            std=std,
            proj=proj,
            agent_name=agent_id,
            global_config_path=system_settings_path,
            project_toml=project_toml,
            workset_config_path=workset_path,
            agent_config_path=agent_cfg_path,
        )

        # Image sharing: mount host image storage read-only into child.
        if share_images or merged.box_share_images:
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

        # Peer communication: the channel system (5 types, 2 scopes — TARGET §2f).
        # Replaces the single legacy ``~/comms`` mount with per-mode channel binds
        # surfaced under ``~/channels/`` (system, every mode) + ``~/channels/workset/``
        # (workset-local, primary/named only) + own ``~/channels/inbox``.  The binds
        # flow through the category resolver (D-B1 precedence + L7 guarantee-create);
        # chat ``general.md``/``broadcast.md`` are seeded inside the chat sources.
        channel_mounts = _build_channel_mounts(
            std=std,
            proj=proj,
            agent_name=agent_id,
            global_config_path=system_settings_path,
            project_toml=project_toml,
            workset_config_path=workset_path,
            agent_config_path=agent_cfg_path,
        )
        extra_mounts.extend(channel_mounts)

        # Read environment variables, accumulating across config levels with
        # the settings-framework precedence (low->high): system < agent <
        # workset < box.  Target-derived state env and per-run CLI -e env stay
        # above all config levels.
        global_env_path = std.data_path / "env"
        project_env_path = proj.metadata_path / "env"
        # Workset-level env applies only for a named (non-default) workset
        # group; the default group's tier is already the system env.
        workset_env_path = (
            proj.group.root / "env"
            if (proj.group is not None and not proj.group.is_default)
            else None
        )
        container_env = _build_config_env(
            global_env_path, agent_cfg.env, workset_env_path, project_env_path,
        )
        # Settings-framework env (the `<scope>.env.<VAR>` category) supersedes
        # the retired `.env` files (Phase 2 decision E).  reconcile picks the
        # most-specific scope per VAR (system<agent<workset<box), so applying it
        # over the legacy map is the documented config-level precedence.  It
        # stays BELOW target state env and CLI -e.  ADDITIVE: with no `env.*`
        # keys configured the reconciled env set is empty -> byte-identical.
        container_env.update(
            _resolve_config_env(
                std=std,
                proj=proj,
                agent_name=agent_id,
                global_config_path=system_settings_path,
                project_toml=project_toml,
                workset_config_path=workset_path,
                agent_config_path=agent_cfg_path,
                group_auth=proj.group_auth,
            )
        )
        container_env.update(state_env)                        # target-derived state env

        # Merge per-run -e/--env KEY=VALUE vars (highest priority).
        container_env.update(_parse_cli_env(cli_env))

        # NOTE: Claude Code telemetry (CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC)
        # and the auto-updater disable (DISABLE_AUTOUPDATER) are now carried by
        # claude's descriptor.container_env, merged via state_env above.  The
        # former core `target.name == "claude"` special-case has been removed.

        # Inject instance identity for peer communication.
        if proj.name:
            container_env["KANIBAKO_NAME"] = proj.name

        # Stamp the resolved agent ON THE CONTAINER (NOT durable config — keeps
        # `--agent` ephemeral).  On a later `kanibako start` against this running
        # persistent box, the reattach fast-source reads this back so it can
        # refresh creds + attach without re-running the resolution cascade (which
        # would otherwise Gate-2a when there are 2+ agents and no default).  Only
        # stamped for a real agent launch; no-agent/shell launches (target is
        # None) carry no agent, so the var is left unset.
        if target is not None:
            container_env["KANIBAKO_AGENT"] = target.name

        # Helper hub: start listener before director, mount socket
        hub = None
        helpers_enabled = not no_helpers and merged.allow_helpers

        # Resolve the no-agent box.shell once, up front, so it can be threaded
        # into both the helper context (below) and the main launch decision
        # (further down).  The resolver is cheap/idempotent (reads the stored
        # image shell; no container spin-up once captured) and follows the
        # single-source-of-truth chain: box.shell -> $KANIBAKO_SHELL -> image's
        # recorded login shell -> sh.  Resolve it whenever it could be used: a
        # no-agent launch (the main entrypoint), or any helper spawn (helpers
        # need a shell fallback even under a real-agent director).  A real-agent
        # launch with helpers off never needs it, so skip the resolve there.
        no_agent_launch = not entrypoint and (
            target is None or target.default_entrypoint is None
        )
        if no_agent_launch or helpers_enabled:
            from kanibako.shells import resolve_box_shell
            box_shell, _box_shell_source = resolve_box_shell(
                merged, std, runtime=runtime, image=image,
            )
        else:
            box_shell = None

        if helpers_enabled:
            from kanibako.helper_listener import HelperContext, HelperHub, MessageLog
            from kanibako.targets.base import Mount as _HMount

            # Socket must live in a short path to stay under the AF_UNIX
            # ``sun_path`` limit.  ``std.runtime`` is ``$XDG_RUNTIME_DIR/kanibako``
            # resolved through the hardened XDG resolver (honor-iff-absolute,
            # with a warn-on-fallback to /run/user/$UID or a 0700 temp dir) —
            # the single source of truth for the runtime base.
            _run_dir = std.runtime
            _run_dir.mkdir(parents=True, exist_ok=True)
            # Socket name = ``<box>-<ws>`` (box name + workset-name token), so a
            # project name reused across worksets gets a distinct socket.  The
            # combined identity is bounded so a long name can't overflow
            # ``sun_path``; reattach recomputes the same deterministic name.
            from kanibako.channels import workset_name_token
            _box_name = proj.name if proj.name else short_hash(proj.project_hash)
            _ws_token = workset_name_token(proj)
            socket_path = _run_dir / bounded_socket_name(
                f"{_box_name}-{_ws_token}", _run_dir,
            )
            validate_socket_path(socket_path)
            # Per-box, per-mode HOST helper log — lives inside the box's own
            # workset/box tree (PRIMARY → primary_workset/logs/<box>.jsonl,
            # NAMED → <workset_root>/logs/<box>.jsonl, STANDALONE →
            # box_data/<box>.jsonl), not the old shared @system.data/logs/<id>/
            # location.  Guarantee-create the parent before the ro bind (L7).
            from kanibako.paths import helper_log_path
            log_path = helper_log_path(std, proj)
            log_path.parent.mkdir(parents=True, exist_ok=True)

            # Ensure helpers/ dir exists in shell_path
            helpers_dir = proj.shell_path / "helpers"
            helpers_dir.mkdir(exist_ok=True)

            # Build context for helper container launches.  Helpers reuse the
            # agent's delivery binds so an in-helper agent finds the same binary;
            # `binary_mnts` is the descriptor-assembled delivery mount list built
            # above (only set when a descriptor-bearing target has a host
            # install — NoAgentTarget has none, so helpers get just the kanibako
            # binds).
            binary_mounts = list(kanibako_mnts)
            if target and install:
                binary_mounts.extend(binary_mnts)

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
                box_shell=box_shell,
                project_path=proj.project_path,
                data_path=std.data_path,
                boxes=std.boxes,
            )

            msg_log = MessageLog(log_path)
            hub = HelperHub()
            hub.start(socket_path, helper_ctx, log=msg_log)

            # Box-side socket + log dests are XDG_STATE_HOME-aware: derived from
            # the BOX's container env (honor-iff-absolute, else
            # ``$HOME/.local/state``), the single derivation shared with
            # ``helper-init.sh`` (`${XDG_STATE_HOME:-$HOME/.local/state}`) and
            # the in-box CLI (``xdg``) so host and box agree by construction.
            box_state_kanibako = box_state_home(container_env) / "kanibako"

            # Mount the socket into the container (only if hub started)
            kanibako_dir = proj.shell_path / ".local" / "state" / "kanibako"
            kanibako_dir.mkdir(parents=True, exist_ok=True)
            if socket_path.exists():
                extra_mounts.append(_HMount(
                    source=socket_path,
                    destination=str(box_state_kanibako / "helper.sock"),
                    options="",
                ))

            # Mount the helper message log for the in-box `log` command
            if log_path.exists():
                extra_mounts.append(_HMount(
                    source=log_path,
                    destination=str(box_state_kanibako / "helpers.jsonl"),
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

        # No-agent box: launch the configured box.shell (already resolved above
        # via the single-source-of-truth chain box.shell -> $KANIBAKO_SHELL ->
        # stored image shell -> sh, and threaded into the helper context).  A
        # real agent sets a non-None entrypoint here and is left untouched —
        # box_shell only feeds the no-agent launch below.

        # In-box setup (FIX 2): the pre-launch auth probe failed AND the target
        # declares an interactive setup command (goose ``configure`` / codex
        # ``login``).  Run it FOREGROUND in the now-assembled box (same
        # image/mounts/tmpfs/env as the real launch) so the user can configure /
        # log in IN THE BOX; the result lands in box-state and persists across
        # reattach (1.6.0 "no host-config import" design).
        #
        # GATING (refined FIX 2): the setup command's exit code is treated as a
        # CRASH check ONLY — a non-zero exit means the setup itself failed/aborted,
        # so we fast-fail and do NOT launch.  A clean (rc 0) exit does NOT prove a
        # bootable config (partial-config case), so we DON'T gate on it here: we
        # proceed to the REAL launch and let it validate the config.  A
        # post-launch :meth:`Target.should_run_setup` match against the session
        # logs (below, at the same persistent-path site as the resume retry) is
        # the ground-truth detector for "config did not take".  ``check_auth`` is
        # HOST-side and cannot see box-only state (goose/codex), so it is NOT
        # re-probed on the launch path.
        if target is not None and needs_inbox_setup:
            setup_ep = target.setup_entrypoint
            assert setup_ep is not None  # needs_inbox_setup implies it is set
            setup_args = list(target.setup_args)
            print(
                f"{target.display_name} is not configured; running "
                f"'{setup_ep} {' '.join(setup_args)}' in the box. "
                f"Complete the prompts to continue.",
                file=sys.stderr,
            )
            setup_rc = _run_setup_command(
                runtime=runtime,
                image=image,
                proj=proj,
                container_name=container_name,
                setup_entrypoint=setup_ep,
                setup_args=setup_args,
                extra_mounts=extra_mounts,
                tmpfs_masks=tmpfs_masks,
                container_env=container_env,
            )
            # Fast-fail: the setup command CRASHED (non-zero exit) -> don't launch.
            if setup_rc != 0:
                print(
                    f"Error: {target.display_name} setup did not complete "
                    f"(exit {setup_rc}).\n"
                    "  Re-run:      kanibako start\n"
                    "  Re-auth:     kanibako agent reauth\n"
                    "  Skip agent:  kanibako shell",
                    file=sys.stderr,
                )
                return setup_rc or 1
            if setup_only:
                # ``agent reauth`` path (setup_only): run the in-box setup and stop
                # — do NOT drop into a full agent session, and there is no launch
                # to validate against, so this path CANNOT use launch-detection.
                # A clean setup exit (rc 0) is success.  Best-effort host re-probe:
                # if the setup also updated host-readable state, surface a positive
                # confirmation; otherwise just report setup complete (box-only
                # state — host ``check_auth`` legitimately cannot see it).
                if target.check_auth():
                    print(
                        f"{target.display_name}: authenticated.", file=sys.stderr,
                    )
                else:
                    print(
                        f"{target.display_name}: setup complete.", file=sys.stderr,
                    )
                return 0

        # ``agent reauth`` reaches here only when setup was NOT needed (auth was
        # already OK); nothing more to do — don't launch a session.
        if setup_only:
            return 0

        # Persistent mode: wrap command with the configured bootstrap program
        if persistent:
            # box_shell is None only on a real-agent launch, but that path
            # guarantees a non-None entrypoint (set above), so inner_cmd is
            # always a str; mypy can't track that cross-variable invariant.
            inner_cmd = entrypoint or box_shell
            assert inner_cmd is not None
            entrypoint, cli_args = _bootstrap_wrap(
                bootstrap_program, inner_cmd, list(cli_args or []),
            )
        elif not entrypoint:
            # Non-persistent no-agent launch: run box.shell explicitly instead
            # of deferring to the image's default entrypoint.
            entrypoint = box_shell

        try:
            # Run the container
            rc = runtime.run(
                image,
                shell_path=proj.shell_path,
                project_path=proj.project_path,
                vault_ro_path=proj.vault_ro_path,
                vault_rw_path=proj.vault_rw_path,
                extra_mounts=extra_mounts or None,
                tmpfs_masks=tmpfs_masks or None,
                enable_vault=proj.enable_vault,
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
            # Wait briefly for the detached container to start, then verify
            # it's actually running before trying to exec into it.
            import time
            for _attempt in range(10):
                if runtime.is_running(container_name):
                    break
                time.sleep(0.3)
            else:
                # Container never started or exited immediately. If the
                # target says this is recoverable (e.g. "no conversation
                # to continue"), retry with a fresh session before bailing.
                logs = _container_logs(runtime, container_name)
                if logs:
                    print(logs, file=sys.stderr)
                if (
                    target
                    and not new_session
                    and not _is_retry
                    and logs
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
                        autonomous=autonomous,
                        resume_mode=False,
                        extra_args=extra_args,
                        no_helpers=no_helpers,
                        no_auto_auth=no_auto_auth,
                        browser=browser,
                        share_images=share_images,
                        persistent=persistent,
                        model_override=model_override,
                        cli_env=cli_env,
                        explicit_agent=explicit_agent,
                        _is_retry=True,
                    )
                # FIX 2 (launch-validation): the launched session is GROUND TRUTH
                # for a bootable config.  If its logs say the agent is still not
                # configured/authenticated, the in-box setup did NOT take.  This is
                # BOUNDED — setup already ran once this invocation, so we only ERROR
                # here, never loop back into setup.
                if target and logs and target.should_run_setup(logs):
                    _print_setup_did_not_take(target)
                    return 1
                print(
                    "Error: Container exited before session could attach.\n"
                    "Check the logs above, or run 'kanibako system diagnose'.",
                    file=sys.stderr,
                )
                return 1

            # Attach to the new bootstrap session.  The container may not be
            # fully ready for exec even though is_running() returned True
            # (podman race: "container state improper").  Retry a few times.
            _max_exec_attempts = 5
            for _exec_attempt in range(1, _max_exec_attempts + 1):
                rc = runtime.exec(
                    container_name, _bootstrap_attach(bootstrap_program)
                )
                if rc == 0:
                    break
                # Non-zero exit — check if the container is still alive.
                if not runtime.is_running(container_name):
                    # Container died; fall through to the log-showing code.
                    break
                # Container still running but exec failed (transient race).
                if _exec_attempt < _max_exec_attempts:
                    print(
                        f"Warning: container not ready for exec "
                        f"(attempt {_exec_attempt}/{_max_exec_attempts}), "
                        f"retrying...",
                        file=sys.stderr,
                    )
                    time.sleep(0.5)
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
                            autonomous=autonomous,
                            resume_mode=False,
                            extra_args=extra_args,
                            no_helpers=no_helpers,
                            no_auto_auth=no_auto_auth,
                            browser=browser,
                            share_images=share_images,
                            persistent=persistent,
                            model_override=model_override,
                            cli_env=cli_env,
                            explicit_agent=explicit_agent,
                            _is_retry=True,
                        )
                # FIX 2 (launch-validation): the launched session is GROUND TRUTH
                # for a bootable config.  If its logs say the agent is still not
                # configured/authenticated, the in-box setup did NOT take.  BOUNDED
                # — setup already ran once this invocation, so we only ERROR here,
                # never loop back into setup.  Still write back first: a partial
                # in-box login may have produced credentials worth propagating.
                if target and logs and target.should_run_setup(logs):
                    writeback_session_credentials(target, proj)
                    _print_setup_did_not_take(target)
                    return 1

            # FIX 1: writeback on the persistent session-end paths — DETACH
            # (container still running) AND clean exit (container stopped).  The
            # box's home is a host mount, so the in-box creds are readable
            # whether or not the container is still up; both are writeback
            # moments so an in-box login reaches the host.  (The new-session
            # retry above returns early and re-enters this function, which writes
            # back on its own teardown.)
            writeback_session_credentials(target, proj)
        else:
            # Clean/ephemeral exit: writeback project -> host (FIX 1 helper).
            writeback_session_credentials(target, proj)

            # Hint when agent exits non-zero and --continue/--resume was used
            if rc != 0 and is_agent_mode and not new_session:
                print(
                    "hint: if the agent exited because there was no conversation "
                    "to continue, use 'kanibako start -N' to start fresh.",
                    file=sys.stderr,
                )

        # Surface any tier-2 baseline warnings now that the bootstrap session
        # has closed (the alt-screen has been torn down).
        _print_launch_issues(std, container_name)

        return rc

    finally:
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()


def _print_setup_did_not_take(target) -> None:
    """Error for the FIX-2 post-launch detection: the in-box config didn't boot.

    The launch is ground truth — its logs matched :meth:`Target.should_run_setup`,
    so the setup we already ran this invocation did not produce a working
    configuration.  This is the BOUNDED terminus: we surface remediation and
    return; we never loop back into setup.
    """
    print(
        f"Error: {target.display_name} setup did not produce a working "
        f"configuration.\n"
        "  Re-run:    kanibako start\n"
        "  Re-auth:   kanibako agent reauth\n"
        "  Skip:      kanibako shell",
        file=sys.stderr,
    )


def writeback_session_credentials(target, proj) -> None:
    """Project -> host credential writeback for a finished/detached session.

    The SINGLE writeback site for ALL session-end paths (FIX 1): clean exit,
    DETACH, reattach-exit, and ``kanibako stop``.  An in-box login must reach the
    host regardless of how the session ends, so every path that releases a box
    funnels through here.

    Gated on ``proj.group_auth`` (the shared-auth contract — default True): under
    distinct auth, the box's credentials are private to the project and never
    propagate to the host.  No-ops when *target* is None (no-agent box) or has no
    credential lifecycle.

    Writes the descriptor's SYNC ``cred_files`` back (creating a missing host
    destination — a deauthed host has no ``~/.claude/.credentials.json``) and the
    plugin's :meth:`~kanibako.targets.base.Target.writeback_extra` (claude merges
    ``oauthAccount`` from the box's ``.claude.json`` into the host's without
    clobbering machine-specific fields).  Best-effort: a writeback failure must
    never crash the lifecycle path that called it.
    """
    if target is None or not getattr(proj, "group_auth", False):
        return
    desc = target.descriptor
    try:
        if desc is not None:
            credsync.writeback_cred_files(
                desc, target, host_home=Path.home(),
                project_home=proj.shell_path, group_auth=proj.group_auth,
            )
        else:
            target.writeback_credentials(proj.shell_path)
        # Plugin-specific writeback beyond the cred_files specs (e.g. claude's
        # .claude.json oauthAccount merge-back, not modelled as a cred_file
        # because its host->project IMPORT was removed in 1.6.0).
        target.writeback_extra(project_home=proj.shell_path, host_home=Path.home())
    except Exception as exc:  # never crash a teardown path on writeback
        get_logger("start").warning("Credential writeback failed: %s", exc)


def _build_config_env(
    global_env_path,
    agent_env: dict[str, str],
    workset_env_path,
    project_env_path,
) -> dict[str, str]:
    """Layer config-level env vars, low->high: system < agent < workset < box.

    Shared between container launch (start) and ``box config --effective`` so
    the resolved config-env matches exactly. Runtime-only layers (target state
    env, per-run ``-e``) are applied by the caller ON TOP of this and are NOT
    config, so they are excluded here.
    """
    from kanibako.shellenv import read_env_file
    env: dict[str, str] = {}
    env.update(read_env_file(global_env_path))   # system
    env.update(agent_env)                        # agent
    if workset_env_path is not None:
        env.update(read_env_file(workset_env_path))  # workset
    env.update(read_env_file(project_env_path))  # box (highest config level)
    return env


def _build_effective_state(
    target,
    agent_cfg,
    project_toml,
    *,
    global_config_path,
    workset_config_path=None,
) -> dict[str, str]:
    """Resolve effective agent-state via the settings precedence walk.

    Walks four levels MOST-SPECIFIC-FIRST — box > workset > agent > system —
    with the target's declared defaults as a FLOOR (the system level's declared
    defaults).  Sources for each level's ``[agent]`` table:

      * **box**     — ``agent.<name>`` (over ``agent.default``) in settings.yaml
      * **workset** — ``agent.<name>`` in the workset's config.yaml (if any)
      * **agent**   — the agent config's own state dict (already per-agent)
      * **system**  — ``agent.<name>`` in the system settings file
        (``@system.settings`` = ``global/settings.yaml``)
      * **floor**   — target ``setting_descriptors()`` defaults

    The box/workset/system/machine override sections are keyed per agent
    (``agent.<target.name>`` layered over the any-agent ``agent.default`` tier)
    so an override never bleeds across an agent switch; ``agent_cfg.state`` is
    already per-agent (loaded from ``agents/<name>/settings.yaml``).

    Explicit set values beat all declared defaults; the most-specific level
    wins; an explicit ``""`` is terminal (no fall-through to the floor).
    Undeclared keys set anywhere (e.g. ``start_mode``) are passed through.

    Values are used verbatim — no ``@``-ref / ``$var`` / ``~`` expansion.

    With no system/workset ``[agent]`` config (the common case) the walk reduces
    to box > agent > floor, i.e. project override > agent state > target default
    — identical to the prior two-source merge.
    """
    from kanibako.config import machine_config_path, read_agent_settings
    from kanibako.settings_resolve import (
        LevelView,
        ResolveCtx,
        SettingsError,
        _Unset,
        resolve_value,
    )

    descriptors = target.setting_descriptors()
    if not descriptors:
        return dict(agent_cfg.state)

    def _read(path) -> dict[str, str]:
        if not path:
            return {}
        try:
            if not path.exists():
                return {}
            # Agent-keyed: read agent.<agent>.* layered over agent.default.* so an
            # override set for one agent never bleeds onto another after a switch.
            return read_agent_settings(path, target.name)
        except Exception:
            return {}

    # Gather per-level [agent] leaf values.
    box_vals = _read(project_toml)
    ws_vals = _read(workset_config_path)
    agent_vals = dict(agent_cfg.state)
    sys_vals = _read(global_config_path)
    machine_vals = _read(machine_config_path())
    floor = {d.key: d.default for d in descriptors}

    # Most-specific first; the machine (/etc) layer is least-specific and carries
    # the declared-defaults floor (so /etc set-values beat the floor, and the
    # floor remains the ultimate fallback).
    levels = [
        LevelView("box", box_vals),
        LevelView("workset", ws_vals),
        LevelView("agent", agent_vals),
        LevelView("system", sys_vals),
        LevelView("machine", machine_vals, defaults=floor),
    ]

    keys = (
        set(floor)
        | set(box_vals)
        | set(ws_vals)
        | set(agent_vals)
        | set(sys_vals)
        | set(machine_vals)
    )

    ctx = ResolveCtx(
        agent_name=target.name,
        workset_name=None,
        host_home=str(Path.home()),
        xdg={},
    )

    def _no_lookup(ref, chain):
        raise SettingsError(f"@-refs not supported in agent settings: {ref}")

    effective: dict[str, str] = {}
    for key in keys:
        rv = resolve_value(key, levels=levels, ctx=ctx, lookup=_no_lookup)
        if not isinstance(rv, _Unset):
            effective[key] = rv.value

    return effective


def _build_binding_overrides(
    *,
    project_toml,
    workset_config_path,
    agent_config_path,
    global_config_path,
    agent_name: str,
) -> dict[str, str]:
    """Resolve descriptor binding host-source overrides across the config cascade.

    Reads each config level's ``agent.<agent_name>.binding`` sub-table (layered
    over ``agent.default.binding`` within each file) via
    :func:`~kanibako.config.read_binding_overrides`, mirroring B3's agent-keying,
    then overlays the levels MOST-SPECIFIC-WINS:

        box (settings.yaml) > workset > agent (agents/<name>/settings.yaml) > system > machine

    Returns ``{binding_key: host_src}`` (empty when nothing is configured, the
    common case).  A bad/unreadable level contributes nothing (the reader
    swallows its own errors).
    """
    from kanibako.config import machine_config_path, read_binding_overrides

    overrides: dict[str, str] = {}
    # Least-specific first so each more-specific level's .update() wins.
    for path in (
        machine_config_path(),
        global_config_path,
        agent_config_path,
        workset_config_path,
        project_toml,
    ):
        overrides.update(read_binding_overrides(path, agent_name))
    return overrides


def _apply_init_seeds(
    *,
    std,
    proj,
    agent_name: str,
    target=None,
    global_config_path,
    project_toml,
    workset_config_path,
    agent_config_path,
    logger,
    group_auth: bool = True,
) -> None:
    """Copy configured copy-once-at-init seeds into the new project's shell dir.

    ADDITIVE: with no seed config and no target default seeds, copies nothing.
    Routes the category config through the reconcile model
    (:func:`_resolve_launch_categories`) and applies the COPY winners whose
    category is ``seeded``, translating each guest_dest (/home/agent/X) to a host
    path under proj.shell_path and copying host_src -> that path once (dir ->
    copytree dirs_exist_ok; file -> copy2).

    The ``group_auth`` credential gate (D-M4) is applied during reconcile: a
    credential-flagged ``seeded`` entry is suppressed when *group_auth* is False.
    """
    import shutil

    from kanibako.settings_resolve import GUEST_HOME

    default_seeds = target.default_seeds() if target is not None else {}

    reconciled = _resolve_launch_categories(
        std=std,
        proj=proj,
        agent_name=agent_name,
        global_config_path=global_config_path,
        project_toml=project_toml,
        workset_config_path=workset_config_path,
        agent_config_path=agent_config_path,
        default_categories=default_seeds,
        group_auth=group_auth,
    )

    for seed in reconciled.copies:
        if seed.category != "seeded":
            continue  # synced copies are applied by _apply_synced_copies.
        assert seed.host_src is not None  # seeds always have a source.
        gd = seed.box_dest.rstrip("/")
        if gd == GUEST_HOME:
            dest = proj.shell_path
        elif gd.startswith(GUEST_HOME + "/"):
            rel = gd[len(GUEST_HOME) + 1:]
            dest = proj.shell_path / rel
        else:
            logger.warning(
                "seed %s: guest_dest %r is outside %s; skipping",
                seed.name, seed.box_dest, GUEST_HOME,
            )
            continue
        src = Path(seed.host_src)
        if not src.exists():
            logger.warning(
                "seed %s: host_src %r does not exist; skipping",
                seed.name, seed.host_src,
            )
            continue
        if src.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copytree(str(src), str(dest), dirs_exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dest))


def _apply_synced_copies(
    *,
    std,
    proj,
    agent_name: str,
    target=None,
    global_config_path,
    project_toml,
    workset_config_path,
    agent_config_path,
    logger,
    group_auth: bool = True,
) -> None:
    """Apply the ``<scope>.synced.<name>`` category copies into the box shell dir.

    Unlike copy-once seeds, synced entries are reapplied on EVERY launch, but
    only when the host source is NEWER than the box-side copy (mtime gating) so
    an unchanged source is a no-op.  Routes the category config through the
    reconcile model (:func:`_resolve_launch_categories`) and applies the COPY
    winners whose category is ``synced``, translating each guest_dest
    (/home/agent/X) to a host path under ``proj.shell_path``.

    This is the settings-driven synced path, DISTINCT from the plugin
    descriptor's ``cred_files`` credsync engine (descriptor-driven) — the two do
    not overlap, so there is no double application.

    The ``group_auth`` credential gate (D-M4) is applied during reconcile: every
    ``synced`` entry is suppressed when *group_auth* is False.

    ADDITIVE: with no ``synced.*`` keys configured (and no target default synced
    entries) the reconciled copy set has no ``synced`` winners -> copies nothing.
    """
    import shutil

    from kanibako.settings_resolve import GUEST_HOME

    # NOTE: synced entries come only from settings `<scope>.synced.<name>` keys.
    # Plugin descriptors do NOT yet declare default synced entries — that
    # population is Phase 8.  *target* is accepted for call-site symmetry and is
    # unused here until then.
    reconciled = _resolve_launch_categories(
        std=std,
        proj=proj,
        agent_name=agent_name,
        global_config_path=global_config_path,
        project_toml=project_toml,
        workset_config_path=workset_config_path,
        agent_config_path=agent_config_path,
        group_auth=group_auth,
    )

    for sync in reconciled.copies:
        if sync.category != "synced":
            continue  # seeded copies are applied by _apply_init_seeds.
        assert sync.host_src is not None  # synced entries always have a source.
        gd = sync.box_dest.rstrip("/")
        if gd == GUEST_HOME:
            dest = proj.shell_path
        elif gd.startswith(GUEST_HOME + "/"):
            rel = gd[len(GUEST_HOME) + 1:]
            dest = proj.shell_path / rel
        else:
            logger.warning(
                "synced %s: guest_dest %r is outside %s; skipping",
                sync.name, sync.box_dest, GUEST_HOME,
            )
            continue
        src = Path(sync.host_src)
        if not src.exists():
            logger.warning(
                "synced %s: host_src %r does not exist; skipping",
                sync.name, sync.host_src,
            )
            continue
        # mtime gate: skip if the dest is at least as new as the source.
        try:
            if dest.exists() and dest.stat().st_mtime >= src.stat().st_mtime:
                continue
        except OSError:
            pass  # stat failure -> fall through and (re)copy
        if src.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copytree(str(src), str(dest), dirs_exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dest))


# Default vault tmpfs mask (decision B): the local ``~/workspace/vault`` is
# hidden behind an UNCONDITIONAL read-only tmpfs in every box mode.  It flows
# through the category resolver as a ``box.masks`` default so a box may add to
# or suppress (terminal ``""``) it like any other category.  (The old behavior
# was conditional on the default-workset mode; the mask is now unconditional —
# design decision B, design-review m6.  The ``.gitignore`` overlay that rode on
# the old tmpfs is DROPPED.)
VAULT_MASK_DEST = "~/workspace/vault"


def _category_resolution_inputs(
    *,
    std,
    proj,
    agent_name: str,
    global_config_path,
    project_toml,
    workset_config_path,
    agent_config_path,
    default_categories: dict[str, str] | None = None,
):
    """Build the shared (levels, ctx, lookup, scope_roots) for category resolution.

    Reads every level's scope-category keys (the unified
    masks/bindings/caches/seeded/shared/synced/env primitive) from its config
    file and assembles the 5 precedence levels (most-specific first; machine
    ``/etc`` below the system settings tier — ``global_config_path`` =
    ``@system.settings`` = ``global/settings.yaml``).  *default_categories* are
    injected as the AGENT level's declared defaults (e.g. a target's
    ``default_shares()`` / ``default_seeds()`` plus core mask defaults).
    """
    from kanibako.config import machine_config_path, read_categories
    from kanibako.settings_resolve import LevelView, ResolveCtx, SettingsError

    levels = [
        LevelView("box", read_categories(project_toml)),
        LevelView("workset", read_categories(workset_config_path)),
        LevelView(
            "agent",
            read_categories(agent_config_path),
            defaults=default_categories or {},
        ),
        LevelView("system", read_categories(global_config_path)),
        LevelView("machine", read_categories(machine_config_path())),
    ]

    # Source roots per scope group (concrete host paths → expand_expr verbatim).
    # The system-scope binding roots (system.bindings.{ro,rw}) were DELETED in
    # the system.* reorg (subsumed by the workset vault / 'shared' category);
    # only the agent/workset scopes remain here.
    agent_share_root = str(std.agents / agent_name / "share")
    # Agent-scope `shared`/`caches` root at the per-agent store dir
    # ``@agent.<agent>.meta.path`` = ``@system.agents/<agent>`` (e.g. claude's
    # plugins/cache live at ``<data>/agents/claude/{plugins,cache}``).  The
    # category KEY name is the relative ``host_src`` joined under this root.
    agent_store_root = str(std.agents / agent_name)
    scope_roots = {
        "agent.bindings.ro": agent_share_root,
        "agent.bindings.rw": agent_share_root,
        "agent.shared": agent_store_root,
        "agent.caches": agent_store_root,
    }
    if proj.group is not None and not proj.group.is_default:
        ws_root = str(proj.group.root)
        scope_roots["workset.bindings.ro"] = ws_root
        scope_roots["workset.bindings.rw"] = ws_root
    # box scope: arbitrary host path, NO root → omit (host_src used as-is).

    workset_name = (
        proj.group.name
        if (proj.group is not None and not proj.group.is_default)
        else None
    )
    ctx = ResolveCtx(
        agent_name=agent_name,
        workset_name=workset_name,
        host_home=str(Path.home()),
        xdg={"XDG_DATA_HOME": str(std.data_home)},
    )

    # Category VALUES may reference the resolved system config tier via @-refs.
    resolved_sys = {
        "system.data": str(std.data),
        "system.agents": str(std.agents),
        "system.channels": str(std.channels),
        "system.base_template": str(std.base_template),
        "system.registry": str(std.registry),
        "system.primary_workset": str(std.primary_workset),
    }

    def _lookup(ref, chain):
        if ref in resolved_sys:
            return resolved_sys[ref]
        raise SettingsError(f"Unresolvable @-reference in category value: {ref}")

    return levels, ctx, _lookup, scope_roots


def _resolve_launch_categories(
    *,
    std,
    proj,
    agent_name: str,
    global_config_path,
    project_toml,
    workset_config_path,
    agent_config_path,
    default_categories: dict[str, str] | None = None,
    group_auth: bool = True,
):
    """Resolve + reconcile the unified scope-category config for the launch path.

    Returns a :class:`~kanibako.settings_categories.ReconciledCategories`
    (``mounts`` / ``copies`` / ``envs``) with cross-category collisions resolved
    and the ``group_auth`` credential gate applied.  AGENT_CRITICAL agent-binary
    binds do NOT flow through here (decision C — they stay in the separate
    descriptor-mount list that always wins).
    """
    from kanibako.settings_categories import (
        reconcile_categories,
        resolve_categories,
    )

    levels, ctx, lookup, scope_roots = _category_resolution_inputs(
        std=std,
        proj=proj,
        agent_name=agent_name,
        global_config_path=global_config_path,
        project_toml=project_toml,
        workset_config_path=workset_config_path,
        agent_config_path=agent_config_path,
        default_categories=default_categories,
    )
    entries = resolve_categories(
        levels=levels, ctx=ctx, lookup=lookup, scope_roots=scope_roots
    )
    return reconcile_categories(entries, group_auth=group_auth)


def _build_share_mounts(
    *,
    std,
    proj,
    agent_name: str,
    global_config_path,
    project_toml,
    workset_config_path,
    agent_config_path,
    target=None,
    group_auth: bool = True,
) -> list:
    """Resolve the launch path's category MOUNTs (bindings/caches/shared/masks).

    Routes the category-shaped binds through the reconcile model
    (:func:`_resolve_launch_categories`) and emits the reconciled MOUNT winners
    as :class:`~kanibako.targets.base.Mount` objects.  ``masks`` (tmpfs) are
    handled separately by :func:`_resolve_masks` — they are skipped here so
    the host-side guarantee-create only runs on real bind sources.

    ADDITIVE: with no category keys configured (and no target default shares),
    returns only what target defaults declare.  *target*'s ``default_shares()``
    (if a target is given) are injected as the AGENT level's declared defaults.

    L7 (bug-hunt 2026-06-19): every emitted MOUNT whose host source does not
    exist is mkdir'd best-effort (rw binds) or, for ro binds whose source is
    absent, the mount is DROPPED with a warning — a missing source must never be
    passed to rootless podman, which would abort the whole launch.
    """
    default_shares = target.default_shares() if target is not None else {}

    reconciled = _resolve_launch_categories(
        std=std,
        proj=proj,
        agent_name=agent_name,
        global_config_path=global_config_path,
        project_toml=project_toml,
        workset_config_path=workset_config_path,
        agent_config_path=agent_config_path,
        default_categories=default_shares,
        group_auth=group_auth,
    )

    return _emit_reconciled_mounts(reconciled, label="share")


def _emit_reconciled_mounts(reconciled, *, label: str) -> list:
    """Emit the reconciled MOUNT winners as :class:`Mount`s (L7 guarantee-create).

    Shared by :func:`_build_share_mounts` and :func:`_build_channel_mounts`:
    skips ``masks`` (tmpfs, no host source — emitted via :func:`_resolve_masks`);
    for every other MOUNT it mkdir's a missing rw source (L7 guarantee-create) and
    DROPS a ro bind whose source is absent with a warning (a missing source must
    never reach rootless podman, which would abort the launch).  *label* names the
    bind family in the drop warning.
    """
    from pathlib import Path as _Path

    from kanibako.targets.base import Mount

    mounts: list = []
    for e in reconciled.mounts:
        if e.category == "masks":
            # tmpfs masks have no host source; emitted via _resolve_masks.
            continue
        assert e.host_src is not None  # bind-shaped MOUNTs always have a source.
        src = _Path(e.host_src)
        if e.options != "ro":
            # rw bind: create the host source dir if absent (L7 guarantee-create).
            try:
                src.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass  # best-effort; podman will surface a genuinely bad source
        elif not src.exists():
            # ro bind with a missing source: DROP with a warning (L7) instead of
            # letting rootless podman abort the launch on a dangling bind source.
            import logging
            logging.getLogger(__name__).warning(
                "%s %s: read-only source %s does not exist; dropping mount",
                label, e.name, e.host_src,
            )
            continue
        mounts.append(
            Mount(source=src, destination=e.box_dest, options=e.options)
        )
    return mounts


def _resolve_masks(
    *,
    std,
    proj,
    agent_name: str,
    global_config_path,
    project_toml,
    workset_config_path,
    agent_config_path,
) -> list[str]:
    """Resolve the active tmpfs masks (the ``box.masks`` category) as box-dests.

    Masks flow through the category resolver; the unconditional default
    (:data:`VAULT_MASK_DEST` → ``~/workspace/vault``) is injected so the local
    vault is hidden behind a read-only tmpfs in every box mode (decision B).  A
    box may add masks or suppress all of them with a terminal ``""``.  Returns
    the reconciled mask box-dest LIST (``@``-expanded, e.g.
    ``/home/agent/workspace/vault``) — generalizing the old hardcoded single
    ``vault_tmpfs`` flag.  The default (no extra masks) yields exactly
    ``["/home/agent/workspace/vault"]`` so the emitted podman args are
    byte-identical to the pre-list behavior.
    """
    reconciled = _resolve_launch_categories(
        std=std,
        proj=proj,
        agent_name=agent_name,
        global_config_path=global_config_path,
        project_toml=project_toml,
        workset_config_path=workset_config_path,
        agent_config_path=agent_config_path,
        default_categories={"box.masks": VAULT_MASK_DEST},
    )
    return [e.box_dest for e in reconciled.mounts if e.category == "masks"]


# In-box channel mount roots (guest paths).  System channels under
# ``~/channels/``; workset-local channels (primary/named only) under
# ``~/channels/workset/``; own inbox surfaced at ``~/channels/inbox``.
_CH_SYSTEM_BASE = "~/channels"
_CH_WORKSET_BASE = "~/channels/workset"


def _ch_bind(host_src, box_dest: str) -> str:
    """Build a ``host_src:guest_dest`` bind expression for a channel default.

    *host_src* is an already-resolved absolute host path (from the ``channels``
    helpers).  Any literal ``:`` in the host path is escaped so :func:`split_bind`
    splits only at the dest separator; the guest dest is a fixed ``~/channels``
    path with no colon.
    """
    return f"{str(host_src).replace(':', chr(92) + ':')}:{box_dest}"


def _channel_default_categories(std, proj) -> dict[str, str]:
    """Build the per-mode channel bind table as ``default_categories`` (§3/§3a).

    Maps ``box.bindings.rw.<key>`` → a ``host_src:guest_dest`` bind expression for
    every channel surfaced into THIS box.  Injected through the category resolver
    (D-B1 precedence + depth-sort + L7 guarantee-create) exactly like masks/shares.

    ALL MODES (system scope): the five system channel type roots
    (commons/chat/share/mailboxes) plus this box's own inbox double-bind (the SAME
    host source bound at both ``~/channels/inbox`` and
    ``~/channels/mailboxes/<ws>/<self>`` — A2) plus its share_global publication
    dir source.  PRIMARY + NAMED additionally get the three workset-local type
    roots under ``~/channels/workset/``; STANDALONE OMITS them (A10).
    """
    from kanibako import channels as _ch

    addr = _ch.box_channel_addresses(proj, std)

    binds: dict[str, str] = {
        # System channel type roots (every mode).
        "box.bindings.rw.global_commons": _ch_bind(
            std.channels_commons, f"{_CH_SYSTEM_BASE}/commons"
        ),
        "box.bindings.rw.global_chat": _ch_bind(
            std.channels_chat, f"{_CH_SYSTEM_BASE}/chat"
        ),
        "box.bindings.rw.global_share": _ch_bind(
            std.channels_share, f"{_CH_SYSTEM_BASE}/share"
        ),
        "box.bindings.rw.mailboxes": _ch_bind(
            std.channels_mailboxes, f"{_CH_SYSTEM_BASE}/mailboxes"
        ),
        # Own inbox alias (A2): same host dir as mailboxes/<ws>/<self>, surfaced
        # at the (C)-stable path ~/channels/inbox.  The depth-sort lands this
        # deeper dest after ~/channels/mailboxes — both binds are kept.
        "box.bindings.rw.inbox": _ch_bind(
            addr.inbox, f"{_CH_SYSTEM_BASE}/inbox"
        ),
    }

    wch = _ch.workset_channel_paths(proj, std)
    if wch is not None:
        # Workset-local channels (primary + named only; standalone omits).
        binds["box.bindings.rw.workset_commons"] = _ch_bind(
            wch.commons, f"{_CH_WORKSET_BASE}/commons"
        )
        binds["box.bindings.rw.workset_chat"] = _ch_bind(
            wch.chat, f"{_CH_WORKSET_BASE}/chat"
        )
        binds["box.bindings.rw.workset_share"] = _ch_bind(
            wch.share, f"{_CH_WORKSET_BASE}/share"
        )

    return binds


def _seed_channel_files(std, proj) -> None:
    """Guarantee-create the chat log files inside the channel sources (§3c).

    ``chat/general.md`` (default log) + ``chat/broadcast.md`` (reserved broadcast
    log) at the SYSTEM chat dir (every mode); and at the WORKSET chat dir for
    primary/named.  Create-if-absent (idempotent — never overwrites a user-edited
    log); the partition source DIRS themselves are mkdir'd by the L7 rw-branch.
    Keeps ``_rotate_file`` on each ``broadcast.md`` (A5 — parity with the legacy
    ``broadcast.log`` rotation).
    """
    from kanibako import channels as _ch

    chat_dirs = [std.channels_chat]
    wch = _ch.workset_channel_paths(proj, std)
    if wch is not None:
        chat_dirs.append(wch.chat)

    for chat_dir in chat_dirs:
        try:
            chat_dir.mkdir(parents=True, exist_ok=True)
            general = chat_dir / "general.md"
            if not general.exists():
                general.touch()
            broadcast = chat_dir / "broadcast.md"
            if not broadcast.exists():
                broadcast.touch()
            _rotate_file(broadcast)
        except OSError:
            # Best-effort: a genuinely unwritable source surfaces at launch.
            pass


def _build_channel_mounts(
    *,
    std,
    proj,
    agent_name: str,
    global_config_path,
    project_toml,
    workset_config_path,
    agent_config_path,
) -> list:
    """Resolve the channel binds (§3) and emit them as :class:`Mount`s.

    Replaces the legacy single ``~/comms`` mount.  Injects the per-mode channel
    bind table (:func:`_channel_default_categories`) through the category resolver
    as the AGENT level's declared defaults — so the channel binds flow through the
    D-B1 precedence + depth-sort + L7 guarantee-create (mkdir of every rw source,
    including the per-workset partition dirs) exactly like masks/shares.  Seeds the
    chat ``general.md``/``broadcast.md`` files (§3c) before emitting.  A box may
    override or suppress any individual channel bind at a more-specific level.
    """
    _seed_channel_files(std, proj)

    reconciled = _resolve_launch_categories(
        std=std,
        proj=proj,
        agent_name=agent_name,
        global_config_path=global_config_path,
        project_toml=project_toml,
        workset_config_path=workset_config_path,
        agent_config_path=agent_config_path,
        default_categories=_channel_default_categories(std, proj),
    )
    return _emit_reconciled_mounts(reconciled, label="channel")


def _resolve_config_env(
    *,
    std,
    proj,
    agent_name: str,
    global_config_path,
    project_toml,
    workset_config_path,
    agent_config_path,
    group_auth: bool = True,
) -> dict[str, str]:
    """Resolve the ``<scope>.env.<VAR>`` category into a VAR -> value dict.

    Routes the env category through the reconcile model
    (:func:`_resolve_launch_categories`); each :class:`CategoryEntry` of
    delivery ``ENV`` carries the VAR name in ``box_dest`` and the resolved value
    in ``options``.  reconcile already picked the most-specific scope per VAR
    (system<agent<workset<box), so the returned map IS the config-level env at
    the documented precedence.  ADDITIVE: with no ``env.*`` keys configured the
    map is empty (the retired ``.env`` files no longer feed this) -> byte-
    identical to the pre-wiring launch env.
    """
    reconciled = _resolve_launch_categories(
        std=std,
        proj=proj,
        agent_name=agent_name,
        global_config_path=global_config_path,
        project_toml=project_toml,
        workset_config_path=workset_config_path,
        agent_config_path=agent_config_path,
        group_auth=group_auth,
    )
    return {e.box_dest: e.options for e in reconciled.envs}


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


# AF_UNIX ``sun_path`` length limit.  The platform value is 108 bytes on Linux
# and 104 on macOS/BSD.  We use the smaller (104) as a conservative
# cross-platform floor so a socket path validated here is portable to either —
# never the larger value, which would let a Linux-only path slip past and then
# fail on macOS.
_UNIX_SOCKET_PATH_LIMIT = 104


def _run_setup_command(
    *,
    runtime: ContainerRuntime,
    image: str,
    proj,
    container_name: str,
    setup_entrypoint: str,
    setup_args: list[str],
    extra_mounts: list,
    tmpfs_masks,
    container_env: dict[str, str],
) -> int:
    """Run a target's one-time interactive setup command in a fresh box.

    Mirrors the normal launch ``runtime.run`` (same image/mounts/env so the
    setup writes into the box's mounted home), but with the SETUP entrypoint
    (e.g. ``goose configure``) and run in the FOREGROUND (``detach=False``) so
    it inherits stdio and the user can answer its prompts.  Any prior container
    under *container_name* is removed first; the setup container is removed on
    completion so the subsequent normal relaunch starts clean.

    Returns the setup command's exit code.
    """
    # Clear any leftover (exited) container occupying the name.
    if runtime.container_exists(container_name):
        runtime.rm(container_name)
    rc = runtime.run(
        image,
        shell_path=proj.shell_path,
        project_path=proj.project_path,
        vault_ro_path=proj.vault_ro_path,
        vault_rw_path=proj.vault_rw_path,
        extra_mounts=extra_mounts or None,
        tmpfs_masks=tmpfs_masks or None,
        enable_vault=proj.enable_vault,
        env=container_env,
        name=container_name,
        entrypoint=setup_entrypoint,
        cli_args=setup_args or None,
        detach=False,
    )
    # Remove the setup container so the relaunch recreates it fresh.
    if runtime.container_exists(container_name):
        runtime.rm(container_name)
    return rc


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


# Length (hex chars) of the bounded hash fallback for an over-long box name.
# 16 hex chars = 64 bits of a SHA-256 prefix: ample collision resistance for
# the per-user set of boxes while keeping the basename tiny (``<16>.sock``).
_SOCKET_HASH_LEN = 16


def bounded_socket_name(identity: str, run_dir: Path) -> str:
    """Return a bounded, deterministic ``.sock`` basename for *identity*.

    The host helper socket lives at ``run_dir / <name>``.  *identity* is the
    combined ``<box>-<ws>`` string (box name + workset-name token, per
    ``@system.runtime/<box>-<ws>.sock``) — the box name alone is NOT unique
    across worksets that reuse a project name, so the ws token is required.
    When ``<identity>.sock`` fits under the AF_UNIX limit at *run_dir* it is
    used verbatim; otherwise the name is replaced by a fixed-width SHA-256
    prefix of *identity* (deterministic per identity — so a later reattach
    computes the same socket — and collision-safe across boxes).
    """
    verbatim = f"{identity}.sock"
    if len(str(run_dir / verbatim)) < _UNIX_SOCKET_PATH_LIMIT:
        return verbatim
    return f"{short_hash(project_hash(identity), _SOCKET_HASH_LEN)}.sock"


def validate_socket_path(socket_path: Path) -> None:
    """Raise ValueError if *socket_path* exceeds the AF_UNIX length limit."""
    path_len = len(str(socket_path))
    if path_len >= _UNIX_SOCKET_PATH_LIMIT:
        raise ValueError(
            f"Socket path too long ({path_len} >= {_UNIX_SOCKET_PATH_LIMIT}): "
            f"{socket_path}"
        )

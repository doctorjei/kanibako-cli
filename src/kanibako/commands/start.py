"""kanibako start / shell: container launch with credential flow."""

from __future__ import annotations

import argparse
import fcntl
import os
import shutil
import subprocess
import sys
from pathlib import Path

from kanibako.agent_config import load_agent_config, write_agent_config
from kanibako.commands.diagnose import probe_missing_executables
from kanibako.config import config_file_path, load_config, load_merged_config
from kanibako.container import ContainerRuntime
from kanibako.errors import ContainerError
from kanibako.log import get_logger
from kanibako.rig_registry import load_registry, registry_path
from kanibako.rig_resolve import resolve_rig
from kanibako.paths import (
    _upgrade_shell,
    xdg,
    load_std_paths,
    resolve_any_project,
)
from kanibako.targets import assembly, credsync, resolve_target
from kanibako.targets.assembly import BindingSourceError, descriptor_mounts
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
    project_dir = getattr(args, "project", None)
    agent_args = getattr(args, "agent_args", [])

    # Map -A/-S to safe_mode: -A means autonomous (safe_mode=False),
    # -S means secure (safe_mode=True). Neither means autonomous (default).
    safe_mode = secure
    autonomous = getattr(args, "autonomous", False)

    # Check for agent before launching container.
    # If no agent is detected, show a helpful message instead of silently
    # launching a plain shell.  run_shell() is not affected.
    from kanibako.targets.no_agent import NoAgentTarget
    from kanibako.config import resolve_box_agent
    _cfg_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    try:
        target = resolve_target(resolve_box_agent(None, _cfg_file))
    except KeyError:
        # A configured system.default_agent whose plugin is not installed:
        # defer the actionable error to the real launch path; fall back to
        # auto-detect for this pre-launch guard so the message stays friendly.
        target = resolve_target()
    if isinstance(target, NoAgentTarget):
        print()
        print("No agents detected.")
        print()
        print("  Install a plugin:  pip install kanibako-agent-claude")
        print("  Run setup wizard:  kanibako setup")
        print("  Health check:      kanibako system diagnose")
        print("  Plain sandbox:     kanibako shell")
        print()
        return 0

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
    )


def run_shell(args: argparse.Namespace) -> int:
    project_dir = getattr(args, "project", None)
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
    _is_retry: bool = False,
) -> int:
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)

    std = load_std_paths(config)

    proj = resolve_any_project(std, config, project_dir, initialize=True)

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
    project_toml = proj.metadata_path / "project.yaml"
    workset_path = (proj.group.root / "config.yaml") if proj.group is not None else None
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

    # Detect container runtime and ensure image is available
    try:
        runtime = ContainerRuntime()
    except ContainerError:
        print(
            "Error: No container runtime found.\n"
            "Install podman (https://podman.io/) or Docker, then try again.",
            file=sys.stderr,
        )
        return 1

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

    # Resolve target (agent plugin) and detect installation
    logger = get_logger("start")
    is_agent_mode = entrypoint is None and not box_shell_mode
    target = None
    install = None
    if is_agent_mode:
        from kanibako.config import resolve_box_agent
        agent_name = resolve_box_agent(merged.box_agent, config_file)
        try:
            target = resolve_target(agent_name)
        except KeyError as e:
            print(
                f"Error: {e}\n"
                f"Run 'kanibako agent list' to see available agents, or\n"
                f"'kanibako system diagnose' for a full health check.",
                file=sys.stderr,
            )
            return 1
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

    # Load agent config
    agent_id = target.name if target else "general"
    agent_cfg_path = std.agents / f"{agent_id}.yaml"
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
    # targets keep the per-plugin init_home/refresh/writeback hooks.
    desc = target.descriptor if target else None

    # Persistent mode: reattach if already running, clean up stale containers
    if persistent:
        if runtime.is_running(container_name):
            # Refresh credentials before reattaching
            if target and proj.group_auth:
                if desc is not None:
                    credsync.refresh_cred_files(
                        desc, target, host_home=Path.home(),
                        project_home=proj.shell_path, group_auth=proj.group_auth,
                    )
                else:
                    target.refresh_credentials(proj.shell_path)
            return runtime.exec(
                container_name, _bootstrap_attach(bootstrap_program)
            )
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

        # Template application + agent init for new projects.
        if proj.is_new and target:
            from kanibako.templates import apply_shell_template
            templates_base = std.templates
            # Ensure the agent-specific template variant directory exists.
            (templates_base / target.name / agent_cfg.shell).mkdir(parents=True, exist_ok=True)
            apply_shell_template(proj.shell_path, templates_base, target.name, agent_cfg.shell)
            if desc is not None:
                credsync.seed_cred_files(
                    desc, target, host_home=Path.home(),
                    project_home=proj.shell_path, group_auth=proj.group_auth,
                )
            else:
                target.init_home(proj.shell_path, group_auth=proj.group_auth)

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

        # Copy-once-at-init seeds (additive; overlays templates). target may be
        # None (no agent) — seeds can still come from config levels.
        if proj.is_new:
            _apply_init_seeds(
                std=std, proj=proj, agent_name=agent_id, target=target,
                global_config_path=config_file, project_toml=project_toml,
                workset_config_path=workset_path, agent_config_path=agent_cfg_path,
                logger=logger,
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

        # Pre-launch auth check (skip for distinct auth — creds live in project)
        if target and install and proj.group_auth:
            if not target.check_auth():
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
                global_config_path=config_file,
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
                # Legacy path (unchanged): apply_state + build_cli_args.
                state_args, state_env = target.apply_state(effective_state)
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
                # SHARED_STORE bindings (e.g. claude's AGENT-scope `plugins`
                # share) resolve under the agent-namespaced global shared store
                # `global_shared/<agent_id>/<src_rel>`, unifying with SHARED
                # resources.  When the project has NO global shared store,
                # shared_store_root stays None and descriptor_mounts SKIPS those
                # bindings (the no-shared-store case, unchanged).
                shared_store_root = (
                    proj.global_shared_path / agent_id
                    if proj.global_shared_path
                    else None
                )
                # Per-agent binding host-source overrides (agent.<name>.binding.<key>
                # layered over agent.default.binding) resolved across the config
                # cascade; an override redirects (and always wins for) a binding's
                # host source.
                binding_overrides = _build_binding_overrides(
                    project_toml=project_toml,
                    workset_config_path=workset_path,
                    agent_config_path=agent_cfg_path,
                    global_config_path=config_file,
                    agent_name=agent_id,
                )
                # The `plugins` binding is rw (ro=False) AGENT scope, and
                # descriptor_mounts SKIPS a MISSING agent source.  Pre-create the
                # resolved host source dir best-effort (mirrors the legacy
                # rw-share behavior) so podman binds a real, PERSISTENT dir
                # instead of stubbing it.  A bad source must never crash launch.
                plugins_src = binding_overrides.get("plugins")
                if not plugins_src and shared_store_root is not None:
                    plugins_src = str(shared_store_root / "plugins")
                if plugins_src:
                    try:
                        Path(plugins_src).mkdir(parents=True, exist_ok=True)
                    except OSError:
                        pass
                try:
                    binary_mnts = descriptor_mounts(
                        desc, install,
                        shared_store_root=shared_store_root,
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
            else:
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
                # Safe-fail: every agent bind source must still exist at mount
                # time.  If the host churned (prune/repoint mid-flight) between
                # detection and here, fail with a clean, actionable kanibako
                # error instead of letting crun crash on a dangling bind source.
                missing = [m for m in binary_mnts if not m.source.exists()]
                if missing:
                    detail = "\n".join(
                        f"  {m.source} → {m.destination}" for m in missing
                    )
                    print(
                        f"Error: {target.display_name} mount source disappeared "
                        f"before launch:\n{detail}\n"
                        f"The host agent install changed while starting (e.g. an "
                        f"update pruned a version). Retry the launch.\n"
                        f"Run 'kanibako system diagnose' for a full health check.",
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

        # Scoped bindings (settings-framework {scope}.bindings.{ro,rw}.*).
        # Additive: empty config → no mounts → no behavior change.
        share_mounts = _build_share_mounts(
            std=std,
            proj=proj,
            agent_name=agent_id,
            global_config_path=config_file,
            project_toml=project_toml,
            workset_config_path=workset_path,
            agent_config_path=agent_cfg_path,
            target=target,
        )
        extra_mounts.extend(share_mounts)

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

        # Peer communication: mount shared comms directory.
        from kanibako.targets.base import Mount as _CMount
        comms_path = std.comms
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
                box_shell=box_shell,
                project_path=proj.project_path,
                data_path=std.data_path,
                boxes=std.boxes,
            )

            msg_log = MessageLog(log_path)
            hub = HelperHub()
            hub.start(socket_path, helper_ctx, log=msg_log)

            # Mount the socket into the container (only if hub started)
            kanibako_dir = proj.shell_path / ".local" / "state" / "kanibako"
            kanibako_dir.mkdir(parents=True, exist_ok=True)
            if socket_path.exists():
                extra_mounts.append(_HMount(
                    source=socket_path,
                    destination="/home/agent/.local/state/kanibako/helper.sock",
                    options="",
                ))

            # Mount helper-messages.jsonl for log command inside container
            if log_path.exists():
                extra_mounts.append(_HMount(
                    source=log_path,
                    destination="/home/agent/.local/state/kanibako/helper-messages.jsonl",
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
                vault_tmpfs=(proj.group is not None and proj.group.is_default),
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
                        _is_retry=True,
                    )
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
                            _is_retry=True,
                        )
        else:
            # Write back refreshed credentials via target (skip for distinct auth)
            if target and proj.group_auth:
                if desc is not None:
                    credsync.writeback_cred_files(
                        desc, target, host_home=Path.home(),
                        project_home=proj.shell_path, group_auth=proj.group_auth,
                    )
                else:
                    target.writeback_credentials(proj.shell_path)

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

      * **box**     — ``agent.<name>`` (over ``agent.default``) in project.yaml
      * **workset** — ``agent.<name>`` in the workset's config.yaml (if any)
      * **agent**   — the agent config's own state dict (already per-agent)
      * **system**  — ``agent.<name>`` in the global kanibako.yaml
      * **floor**   — target ``setting_descriptors()`` defaults

    The box/workset/system/machine override sections are keyed per agent
    (``agent.<target.name>`` layered over the any-agent ``agent.default`` tier)
    so an override never bleeds across an agent switch; ``agent_cfg.state`` is
    already per-agent (loaded from ``agents/<name>.yaml``).

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

        box (project.yaml) > workset > agent (agents/<name>.yaml) > system > machine

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
) -> None:
    """Copy configured copy-once-at-init seeds into the new project's shell dir.

    ADDITIVE: with no seed config and no target default seeds, copies nothing.
    Resolves {scope}.seeded.* across the 4 levels (target.default_seeds()
    as the agent level's declared defaults), translates each SeedPair's
    guest_dest (/home/agent/X) to a host path under proj.shell_path, and copies
    host_src -> that path once (dir -> copytree dirs_exist_ok; file -> copy2).
    """
    import shutil

    from kanibako.config import machine_config_path, read_seeds
    from kanibako.settings_resolve import (
        GUEST_HOME,
        LevelView,
        ResolveCtx,
        SettingsError,
    )
    from kanibako.settings_seeds import resolve_seeds

    default_seeds = target.default_seeds() if target is not None else {}

    # Five precedence levels, most-specific first; agent carries the target's
    # declared seed defaults, and machine (/etc) is the least-specific file
    # source below the user-global system config.
    levels = [
        LevelView("box", read_seeds(project_toml)),
        LevelView("workset", read_seeds(workset_config_path)),
        LevelView("agent", read_seeds(agent_config_path), defaults=default_seeds),
        LevelView("system", read_seeds(global_config_path)),
        LevelView("machine", read_seeds(machine_config_path())),
    ]

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
        raise SettingsError(f"Unresolvable @-reference in seed value: {ref}")

    seeds = resolve_seeds(levels=levels, ctx=ctx, lookup=_lookup)

    for seed in seeds:
        gd = seed.guest_dest.rstrip("/")
        if gd == GUEST_HOME:
            dest = proj.shell_path
        elif gd.startswith(GUEST_HOME + "/"):
            rel = gd[len(GUEST_HOME) + 1:]
            dest = proj.shell_path / rel
        else:
            logger.warning(
                "seed %s: guest_dest %r is outside %s; skipping",
                seed.name, seed.guest_dest, GUEST_HOME,
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
) -> list:
    """Resolve scoped-binding config ({scope}.bindings.{ro,rw}.*) into Mounts.

    ADDITIVE: with no share keys configured (and no target default shares),
    returns []. Reads each level's set share keys from its config file; the
    KEY's scope sets the source root + mode, the LEVEL where set decides
    precedence (a box can suppress an inherited system share with a terminal "").

    *target*'s ``default_shares()`` (if a target is given) are injected as the
    AGENT level's *declared defaults*: they mount unless overridden/suppressed at
    a more-specific level. After resolution, host source directories for any
    read-write share are created best-effort (mirrors the old SHARED-mount
    behavior) so podman does not stub them; a bad source never crashes launch.
    """
    from kanibako.config import machine_config_path, read_shares
    from kanibako.settings_resolve import LevelView, ResolveCtx, SettingsError
    from kanibako.settings_shares import resolve_shares

    default_shares = target.default_shares() if target is not None else {}

    # Five precedence levels, most-specific first; agent carries the target's
    # declared share defaults, and machine (/etc/kanibako/kanibako.yaml) is the
    # least-specific file source below the user-global system config.  (The
    # additive image-baseline overlay lives separately in baseline.load_baseline,
    # which reads /etc/kanibako/image-baseline.yaml.)
    levels = [
        LevelView("box", read_shares(project_toml)),
        LevelView("workset", read_shares(workset_config_path)),
        LevelView("agent", read_shares(agent_config_path), defaults=default_shares),
        LevelView("system", read_shares(global_config_path)),
        LevelView("machine", read_shares(machine_config_path())),
    ]

    # Source roots per scope group (concrete host paths → expand_expr verbatim).
    # The system-scope binding roots (system.bindings.{ro,rw}) were DELETED in
    # the system.* reorg (subsumed by the workset vault / 'shared' category);
    # only the agent/workset scopes remain here.
    agent_share_root = str(std.agents / agent_name / "share")
    scope_roots = {
        "agent.bindings.ro": agent_share_root,
        "agent.bindings.rw": agent_share_root,
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

    # Share VALUES may reference the resolved system config tier via @-refs.
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
        raise SettingsError(f"Unresolvable @-reference in share value: {ref}")

    mounts = resolve_shares(
        levels=levels, ctx=ctx, lookup=_lookup, scope_roots=scope_roots
    )
    for m in mounts:
        if m.options != "ro":  # rw share: create the host source dir if absent
            try:
                m.source.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass  # best-effort; podman will surface a genuinely bad source
    return mounts


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

    project_toml = proj.metadata_path / "project.yaml"
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

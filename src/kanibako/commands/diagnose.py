"""kanibako diagnose: system and per-scope health checks."""

from __future__ import annotations

import shutil
from pathlib import Path

from kanibako.targets.base import _validate_agent_binary


def _format_check(status: str, label: str, detail: str) -> str:
    """Format a single diagnostic check line."""
    return f"[{status}] {label}: {detail}"


# ---------------------------------------------------------------------------
# Baseline probing (shared by `kanibako rig diagnose` and the launch path)
# ---------------------------------------------------------------------------

# Sentinel emitted (one per line) for each present executable in the probe
# script, so the result can be partitioned without spinning one container per
# executable.
_PROBE_HIT_PREFIX = "KANIBAKO_HAS:"


def probe_missing_executables(
    runtime, image: str, executables: list[str],
) -> list[str]:
    """Return the subset of *executables* missing from *image*.

    Runs ONE ephemeral container
    (``<runtime> run --rm --entrypoint sh <image> -lc ...``) that loops over
    every executable and prints a hit marker for each present one; the misses
    are whatever wasn't reported.  This keeps the cost to a single container
    spin-up regardless of how many executables are checked.

    The ``--entrypoint sh`` override is essential: kanibako images set an
    ENTRYPOINT (``kanibako-entrypoint``) that would otherwise swallow the probe
    script, making every executable look missing.

    On any runtime failure (image missing, runtime broken) every executable is
    reported as missing — the caller decides whether that is fatal.
    """
    import subprocess

    if not executables:
        return []
    # Build a portable POSIX-sh loop: for each exe, emit the hit marker iff
    # `command -v` succeeds. Executable names are baseline-controlled (no shell
    # metacharacters), so plain interpolation is safe here.
    checks = "; ".join(
        f'command -v "{exe}" >/dev/null 2>&1 && echo "{_PROBE_HIT_PREFIX}{exe}"'
        for exe in executables
    )
    try:
        result = subprocess.run(
            [runtime.cmd, "run", "--rm", "--entrypoint", "sh", image, "-lc", checks],
            capture_output=True,
            text=True,
        )
    except Exception:
        return list(executables)
    if result.returncode != 0 and not result.stdout:
        # Container could not start at all → treat everything as missing.
        return list(executables)
    present = {
        line[len(_PROBE_HIT_PREFIX):]
        for line in result.stdout.splitlines()
        if line.startswith(_PROBE_HIT_PREFIX)
    }
    return [exe for exe in executables if exe not in present]


def _check_runtime() -> tuple[str, str]:
    """Check container runtime availability. Returns (status, detail)."""
    try:
        import subprocess

        from kanibako.container import ContainerRuntime

        runtime = ContainerRuntime()
        result = subprocess.run(
            [runtime.cmd, "--version"],
            capture_output=True,
            text=True,
        )
        version = (
            result.stdout.strip() if result.returncode == 0 else "unknown version"
        )
        return "ok", f"{runtime.cmd} ({version})"
    except Exception:
        return "!!", "not found -- install podman (https://podman.io/) or Docker"


def _check_image(config: object) -> tuple[str, str]:
    """Check if the configured container image exists locally."""
    try:
        from kanibako.container import ContainerRuntime

        runtime = ContainerRuntime()
        image_name: str = getattr(config, "box_image", "")
        data = runtime.image_inspect(image_name)
        if data is not None:
            return "ok", f"{image_name} (available locally)"
        return (
            "!!",
            f"{image_name} (not found locally -- will be pulled on first use)",
        )
    except Exception:
        return "--", "cannot check (no container runtime)"


# Friendly labels for the box.shell resolver's source token (see
# kanibako.shells.resolve_box_shell), used in the no-agent "Shell" detail line.
_SHELL_SOURCE_LABELS = {
    "box.shell": "box.shell",
    "$KANIBAKO_SHELL": "$KANIBAKO_SHELL",
    "image": "image default",
    "sh": "fallback",
}


def _resolved_shell_detail(config, std, runtime, image) -> str:
    """Return the no-agent "Shell" detail (resolved box.shell + friendly source).

    Defensive: diagnose must NEVER crash on shell resolution.  Without enough
    context (no config/std) or on any failure, falls back to ``sh (fallback)``.
    """
    if config is None or std is None:
        return "sh (fallback)"
    try:
        from kanibako.shells import resolve_box_shell

        shell, source = resolve_box_shell(config, std, runtime=runtime, image=image)
        label = _SHELL_SOURCE_LABELS.get(source, source)
        return f"{shell} ({label})"
    except Exception:
        return "sh (fallback)"


def _check_agents(
    config=None, std=None, runtime=None, image=None,
) -> list[tuple[str, str, str]]:
    """Check all discovered agent targets.

    Returns list of (status, label, detail).  *config*/*std* (and optionally
    *runtime*/*image*) are used only to resolve the no-agent "Shell" target's
    launch shell via :func:`kanibako.shells.resolve_box_shell`.
    """
    from kanibako.targets import discover_targets

    targets = discover_targets()
    results: list[tuple[str, str, str]] = []
    if not targets:
        results.append(("!!", "Agents", "no agent plugins installed"))
        return results
    for name, cls in targets.items():
        try:
            instance = cls()
            if not getattr(instance, "has_binary", True):
                # No-binary fallback (the "Shell" no-agent target): it needs no
                # host binary, so it is ALWAYS available -- never flag it.  Show
                # the resolved launch shell and where it came from.
                detail = _resolved_shell_detail(config, std, runtime, image)
                results.append(("ok", f"Agent: {instance.display_name}", detail))
                continue
            install = instance.detect()
            if install is not None:
                binary = getattr(install, "binary", None)
                # Validate the detected host binary the SAME way the launch
                # path does: a dangling path, a 0-byte file, or a
                # non-executable file are all real errors (a 0-byte binary
                # passes a bare exists() check yet bricks the box at launch).
                reason = (
                    _validate_agent_binary(Path(binary)) if binary else None
                )
                if reason:
                    results.append(
                        (
                            "!!",
                            f"Agent: {instance.display_name}",
                            reason,
                        )
                    )
                else:
                    detail = f"({binary})" if binary else "detected"
                    results.append(("ok", f"Agent: {instance.display_name}", detail))
            else:
                # A real agent that simply isn't installed -- optional, not an
                # error.
                results.append(
                    (
                        "--",
                        f"Agent: {instance.display_name}",
                        "not installed (optional)",
                    )
                )
        except Exception as e:
            results.append(("!!", f"Agent: {name}", str(e)))
    return results


def _check_storage(data_path: Path) -> tuple[str, str]:
    """Check available disk space at the data path."""
    try:
        usage = shutil.disk_usage(data_path)
        free_gb = usage.free / (1024**3)
        total_gb = usage.total / (1024**3)
        if free_gb < 1:
            return (
                "!!",
                f"{free_gb:.1f} GB free of {total_gb:.0f} GB in {data_path}",
            )
        return (
            "ok",
            f"{free_gb:.1f} GB free of {total_gb:.0f} GB in {data_path}",
        )
    except Exception:
        return "--", f"cannot check ({data_path})"


def run_system_diagnose(args: object) -> int:
    """Run full system diagnostics."""
    from kanibako.config import config_file_path, load_config, load_merged_config
    from kanibako.paths import load_std_paths, xdg

    print("Kanibako System Diagnostics")
    print("=" * 40)
    print()

    # Runtime
    status, detail = _check_runtime()
    print(_format_check(status, "Container runtime", detail))

    # Image
    merged = None
    try:
        config_home = xdg("XDG_CONFIG_HOME", ".config")
        cf = config_file_path(config_home)
        merged = load_merged_config(cf, None)
        status, detail = _check_image(merged)
        print(_format_check(status, "Image", detail))
    except Exception:
        print(_format_check("--", "Image", "cannot check (not configured)"))

    # Agents -- resolve the no-agent Shell line with full box.shell precedence
    # (config + std + the configured image; the resolver reads the image-shell
    # store and only probes a container when nothing is stored yet).
    std = None
    runtime = None
    image = None
    try:
        config_home = xdg("XDG_CONFIG_HOME", ".config")
        cf = config_file_path(config_home)
        std = load_std_paths(load_config(cf))
    except Exception:
        std = None
    if merged is not None:
        image = getattr(merged, "box_image", None)
        try:
            from kanibako.container import ContainerRuntime

            runtime = ContainerRuntime()
        except Exception:
            runtime = None
    for status, label, detail in _check_agents(
        config=merged, std=std, runtime=runtime, image=image,
    ):
        print(_format_check(status, label, detail))

    # Storage
    try:
        config_home = xdg("XDG_CONFIG_HOME", ".config")
        cf = config_file_path(config_home)
        config = load_config(cf)
        from kanibako.paths import resolve_system_paths
        data_home = xdg("XDG_DATA_HOME", ".local/share")
        data_path = resolve_system_paths(
            config.system_paths, data_home=data_home, home=Path.home(),
        )["system.data"]
        status, detail = _check_storage(data_path)
        print(_format_check(status, "Storage", detail))
    except Exception:
        print(_format_check("--", "Storage", "cannot check"))

    print()
    return 0


def run_box_diagnose(args: object) -> int:
    """Run diagnostics for a specific project box."""
    from kanibako.config import config_file_path, load_config, read_project_meta
    from kanibako.paths import load_std_paths, resolve_any_project, xdg

    config_home = xdg("XDG_CONFIG_HOME", ".config")
    cf = config_file_path(config_home)
    config = load_config(cf)
    std = load_std_paths(config)

    project_dir = getattr(args, "project", None) or getattr(args, "path", None)
    try:
        proj = resolve_any_project(std, config, project_dir)
    except Exception as e:
        print(f"Error: {e}")
        return 1

    # `resolve_any_project` fabricates a default-mode resolution for ANY
    # existing directory, so a successful return does NOT mean a kanibako
    # project is actually registered at the target.  A real, registered
    # project always has persisted metadata (project.yaml) at its
    # metadata_path; a fabricated one does not.  Without this guard, diagnose
    # would report a meaningless `[ok] Project directory` followed by a false
    # `[!!] Shell directory: missing` for moved/copied/plain directories.
    is_registered = read_project_meta(proj.metadata_path / "project.yaml") is not None
    if not is_registered:
        target = proj.project_path if proj.project_path else project_dir
        print(_format_check("!!", "Project", f"no kanibako project registered for {target}"))
        print(
            "        Run 'kanibako create' to initialize a project here, "
            "or pass a project name/path."
        )
        return 1

    print(f"Box Diagnostics: {proj.project_path}")
    print("=" * 40)
    print()

    # Project directory
    if proj.project_path and proj.project_path.is_dir():
        print(_format_check("ok", "Project directory", str(proj.project_path)))
    else:
        print(_format_check("!!", "Project directory", "missing"))

    # Shell directory: for a registered project, an absent shell is NORMAL
    # before the first launch (it is created on first run / initialize=True),
    # so report it informationally rather than as an error.
    if proj.shell_path and proj.shell_path.is_dir():
        print(_format_check("ok", "Shell directory", str(proj.shell_path)))
    else:
        print(
            _format_check(
                "--", "Shell directory", "not yet initialized (created on first run)",
            )
        )

    # Runtime check
    status, detail = _check_runtime()
    print(_format_check(status, "Container runtime", detail))

    print()
    return 0


def run_agent_diagnose(args: object) -> int:
    """Run diagnostics for agent configuration."""
    from kanibako.config import config_file_path, load_config, load_merged_config
    from kanibako.paths import load_std_paths, xdg

    print("Agent Diagnostics")
    print("=" * 40)
    print()

    # Resolve config/std (and, cheaply, a runtime + configured image) so the
    # no-agent "Shell" line reports its resolved box.shell.  Each step is
    # guarded so a missing config or absent podman never crashes diagnose.
    merged = None
    std = None
    runtime = None
    image = None
    try:
        config_home = xdg("XDG_CONFIG_HOME", ".config")
        cf = config_file_path(config_home)
        std = load_std_paths(load_config(cf))
        merged = load_merged_config(cf, None)
        image = getattr(merged, "box_image", None)
    except Exception:
        pass
    if image is not None:
        try:
            from kanibako.container import ContainerRuntime

            runtime = ContainerRuntime()
        except Exception:
            runtime = None

    for status, label, detail in _check_agents(
        config=merged, std=std, runtime=runtime, image=image,
    ):
        print(_format_check(status, label, detail))

    print()
    return 0


def run_rig_diagnose(args: object) -> int:
    """Run diagnostics for rig/image status."""
    from kanibako.config import config_file_path, load_merged_config
    from kanibako.paths import xdg

    print("Rig (Image) Diagnostics")
    print("=" * 40)
    print()

    status, detail = _check_runtime()
    print(_format_check(status, "Container runtime", detail))

    try:
        config_home = xdg("XDG_CONFIG_HOME", ".config")
        cf = config_file_path(config_home)
        merged = load_merged_config(cf, None)
        status, detail = _check_image(merged)
        print(_format_check(status, "Configured image", detail))
    except Exception:
        print(_format_check("--", "Configured image", "cannot check"))

    # List local images
    try:
        from kanibako.container import ContainerRuntime

        runtime = ContainerRuntime()
        images = runtime.list_local_images()
        if images:
            print(_format_check("ok", "Local images", f"{len(images)} found"))
            for repo, size in images:
                print(f"        {repo}  {size}")
        else:
            print(_format_check("!!", "Local images", "none found"))
    except Exception:
        print(_format_check("--", "Local images", "cannot check"))

    # Baseline executable probe (one ephemeral run per image).
    print()
    _diagnose_baseline(args)

    print()
    return 0


def _diagnose_baseline(args: object) -> None:
    """Probe the image baseline executables and print the result.

    Honors ``--all`` (every local kanibako image), ``--only PKG`` and
    ``--skip PKG`` (default = the single configured ``box_image``, mirroring
    ``rig rebuild``'s default-is-configured-image behavior).  Reuses
    :func:`probe_missing_executables` so a single ephemeral container checks all
    baseline executables per image.
    """
    from kanibako import baseline as baseline_mod
    from kanibako.config import config_file_path, load_merged_config
    from kanibako.container import ContainerRuntime
    from kanibako.paths import xdg

    only = getattr(args, "only", None)
    skip = getattr(args, "skip", None)
    all_images = getattr(args, "all_images", False)

    baseline = baseline_mod.load_baseline()
    pkgs = sorted(baseline)
    if only:
        only_set = set(only)
        pkgs = [p for p in pkgs if p in only_set]
    if skip:
        skip_set = set(skip)
        pkgs = [p for p in pkgs if p not in skip_set]
    wanted_exes: list[str] = [exe for p in pkgs for exe in baseline[p]]
    exe_to_pkg = {exe: p for p in pkgs for exe in baseline[p]}

    print("Baseline:")
    try:
        runtime = ContainerRuntime()
    except Exception:
        print(_format_check("--", "  Baseline", "cannot check (no runtime)"))
        return

    if all_images:
        images = [repo for repo, _size in runtime.list_local_images()]
        if not images:
            print(_format_check("!!", "  Baseline", "no local images to probe"))
            return
    else:
        try:
            config_home = xdg("XDG_CONFIG_HOME", ".config")
            merged = load_merged_config(config_file_path(config_home), None)
            images = [merged.box_image]
        except Exception:
            print(_format_check("--", "  Baseline", "cannot check (not configured)"))
            return

    for image in images:
        missing = probe_missing_executables(runtime, image, wanted_exes)
        if not missing:
            print(_format_check("ok", f"  {image}", "all baseline executables present"))
        else:
            detail = ", ".join(
                f"{exe_to_pkg.get(exe, '?')}:{exe}" for exe in missing
            )
            print(_format_check("!!", f"  {image}", f"missing {detail}"))

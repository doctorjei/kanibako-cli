"""kanibako diagnose: system and per-scope health checks."""

from __future__ import annotations

import shutil
from pathlib import Path


def _format_check(status: str, label: str, detail: str) -> str:
    """Format a single diagnostic check line."""
    return f"[{status}] {label}: {detail}"


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
        image_name: str = getattr(config, "container_image", "")
        data = runtime.image_inspect(image_name)
        if data is not None:
            return "ok", f"{image_name} (available locally)"
        return (
            "!!",
            f"{image_name} (not found locally -- will be pulled on first use)",
        )
    except Exception:
        return "--", "cannot check (no container runtime)"


def _check_agents() -> list[tuple[str, str, str]]:
    """Check all discovered agent targets.

    Returns list of (status, label, detail).
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
            install = instance.detect()
            if install is not None:
                detail_parts: list[str] = []
                binary = getattr(install, "binary", None)
                if binary:
                    detail_parts.append(f"({binary})")
                detail = " ".join(detail_parts) if detail_parts else "detected"
                results.append(("ok", f"Agent: {instance.display_name}", detail))
            else:
                results.append(("!!", f"Agent: {instance.display_name}", "not found"))
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
    from kanibako.paths import xdg

    print("Kanibako System Diagnostics")
    print("=" * 40)
    print()

    # Runtime
    status, detail = _check_runtime()
    print(_format_check(status, "Container runtime", detail))

    # Image
    try:
        config_home = xdg("XDG_CONFIG_HOME", ".config")
        cf = config_file_path(config_home)
        merged = load_merged_config(cf, None)
        status, detail = _check_image(merged)
        print(_format_check(status, "Image", detail))
    except Exception:
        print(_format_check("--", "Image", "cannot check (not configured)"))

    # Agents
    for status, label, detail in _check_agents():
        print(_format_check(status, label, detail))

    # Storage
    try:
        config_home = xdg("XDG_CONFIG_HOME", ".config")
        cf = config_file_path(config_home)
        config = load_config(cf)
        data_home = xdg("XDG_DATA_HOME", ".local/share")
        data_path = data_home / (config.paths_data_path or "kanibako")
        status, detail = _check_storage(data_path)
        print(_format_check(status, "Storage", detail))
    except Exception:
        print(_format_check("--", "Storage", "cannot check"))

    print()
    return 0


def run_box_diagnose(args: object) -> int:
    """Run diagnostics for a specific project box."""
    from kanibako.config import config_file_path, load_config
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

    print(f"Box Diagnostics: {proj.project_path}")
    print("=" * 40)
    print()

    # Project directory
    if proj.project_path and proj.project_path.is_dir():
        print(_format_check("ok", "Project directory", str(proj.project_path)))
    else:
        print(_format_check("!!", "Project directory", "missing"))

    # Shell directory
    if proj.shell_path and proj.shell_path.is_dir():
        print(_format_check("ok", "Shell directory", str(proj.shell_path)))
    else:
        print(_format_check("!!", "Shell directory", "missing or not initialized"))

    # Runtime check
    status, detail = _check_runtime()
    print(_format_check(status, "Container runtime", detail))

    print()
    return 0


def run_crab_diagnose(args: object) -> int:
    """Run diagnostics for agent/crab configuration."""
    print("Crab (Agent) Diagnostics")
    print("=" * 40)
    print()

    for status, label, detail in _check_agents():
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

    print()
    return 0

"""kanibako diagnose: system and per-scope health checks."""

from __future__ import annotations

import shutil
from pathlib import Path

from kanibako.errors import KanibakoError
from kanibako.targets.base import _validate_agent_binary
from kanibako.vscode.vscode_config import load_jsonc as _load_jsonc


def _format_check(status: str, label: str, detail: str) -> str:
    """Format a single diagnostic check line."""
    return f"[{status}] {label}: {detail}"


def _report_settings_error(label: str, err: KanibakoError) -> None:
    """Print *label* as a FAILED check and quote the settings error verbatim.

    The three settings loads in this module used to sit inside a bare
    ``except Exception`` whose only report was ``cannot check (not
    configured)``.  That text is a lie for every error the settings engine
    raises deliberately -- above all the spec §0 refusal of an undeclared key,
    which already names every offending entry and every file the resolve
    loaded.  ``diagnose`` is what a user runs when something is wrong, so
    naming a missing configuration instead of the real refusal points them at
    the WRONG cause.

    ``KanibakoError`` is exactly the right predicate and NOT a discriminator:
    ``errors.py`` defines it as the hierarchy ``cli.py`` catches, i.e. the
    errors whose messages are already written to be shown to a user.  Anything
    else still falls through to the ``(not configured)`` line, which keeps that
    path for the case it was written for.

    The message is reproduced UNCHANGED, one 8-space-indented line each (the
    continuation indent this module already uses); composing a new one here
    would drop the entry names and the file list that make it actionable.
    """
    print(_format_check("!!", label, "settings error -- reported below"))
    for line in str(err).splitlines():
        print(f"        {line}")


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

        from kanibako.runtime.container import ContainerRuntime

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
        from kanibako.runtime.container import ContainerRuntime

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
# kanibako.launch.shells.resolve_box_shell), used in the no-agent "Shell" detail line.
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
        from kanibako.launch.shells import resolve_box_shell

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
    launch shell via :func:`kanibako.launch.shells.resolve_box_shell`.
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


def _check_journal(std, box_key: str | None = None) -> list[tuple[str, str]]:
    """Check the lifecycle journal for lingering in-flight / interrupted ops.

    Reads ``std.journal`` (J1/J2 write-ahead log).  The journal is normally
    EMPTY — an entry is the rare in-flight or crashed lifecycle op (create /
    import / connect) that never cleared.  Each pending entry is reported as a
    WARNING (``!!``) carrying op + box + ``started_at``, so a broken-state box is
    visible; an empty journal is a clean ``ok``.

    *box_key* (per-box diagnose): when given, only the entry keyed by that
    host-side box dir is considered — a clean ``ok`` when that one box has no
    pending op, regardless of unrelated entries elsewhere.  When None (system
    diagnose) every entry is surfaced.

    Returns a LIST of ``(status, detail)`` lines (one per pending entry, or a
    single clean line).  Defensive: any failure degrades to a single ``--``
    ("cannot check") line rather than crashing diagnose.
    """
    try:
        from kanibako.launch import journal

        entries = journal.read_journal(std.journal)
    except Exception:
        return [("--", "cannot read journal")]

    if box_key is not None:
        entry = entries.get(box_key)
        entries = {box_key: entry} if entry is not None else {}

    if not entries:
        return [("ok", "no in-flight operations")]

    lines: list[tuple[str, str]] = []
    for key, entry in sorted(entries.items()):
        op = entry.get("op", "?")
        name = entry.get("name", "?")
        started = entry.get("started_at", "?")
        lines.append((
            "!!",
            f"interrupted {op} of '{name}' at {key} (started {started}) -- "
            "will be completed on next resolve",
        ))
    return lines


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


# ---------------------------------------------------------------------------
# VS Code "Attach to Running Container" host-prerequisite check (feature #1)
# ---------------------------------------------------------------------------

# Extension id of the "Dev Containers" extension that the attach flow driven by
# `kanibako code <box>` (Phase 1) depends on.
_DEVCONTAINERS_EXT_ID = "ms-vscode-remote.remote-containers"




def _check_vscode_docker_path(settings_path: Path) -> tuple[str, str, str]:
    """Check ``dev.containers.dockerPath`` in the user settings.

    Rootless podman is not Docker, so VS Code's Dev Containers must be told to
    drive ``podman`` explicitly.  Two settings are OK:

    * ``"podman"`` — local attach works (a NOTE flags that ``kanibako code
      --remote`` needs the kanibako dispatch wrapper instead);
    * the kanibako dispatch wrapper path — both local AND ``--remote`` work.

    Returns a single ``(status, label, detail)`` line: ``ok`` for either of the
    above; ``!!`` (with remediation) when the file or key is absent or holds
    another value; ``--`` when the file exists but is unreadable / unparseable.
    """
    label = "VS Code dockerPath"
    remediation = (
        'set "dev.containers.dockerPath": "podman" in VS Code user settings.json'
    )
    if not settings_path.is_file():
        return (
            "!!",
            label,
            f"settings.json not found ({settings_path}) -- {remediation}",
        )
    try:
        text = settings_path.read_text(encoding="utf-8")
    except OSError:
        return ("--", label, f"cannot read settings.json ({settings_path})")

    data = _load_jsonc(text)
    if not isinstance(data, dict):
        return (
            "--",
            label,
            f"settings.json present but could not be parsed ({settings_path})",
        )
    value = data.get("dev.containers.dockerPath")
    if value == "podman":
        return (
            "ok",
            label,
            '"dev.containers.dockerPath": "podman" '
            "(local only; 'kanibako code --remote' needs the kanibako wrapper)",
        )
    from kanibako.vscode.vscode_remote import dispatch_wrapper_path

    if value is not None and value == str(dispatch_wrapper_path()):
        return (
            "ok",
            label,
            f'"dev.containers.dockerPath": "{value}" (kanibako dispatch wrapper)',
        )
    if value is None:
        return ("!!", label, f'"dev.containers.dockerPath" not set -- {remediation}')
    return (
        "!!",
        label,
        f'"dev.containers.dockerPath" is "{value}", expected "podman" -- '
        f"{remediation}",
    )


def _check_vscode(config_home: Path | None = None) -> list[tuple[str, str, str]]:
    """Check host prerequisites for VS Code "Attach to Running Container".

    ``kanibako code <box>`` (Phase 1) launches the host VS Code with an
    ``attached-container`` remote URI; that flow needs, host-side: (1) the
    ``code`` CLI on PATH, (2) the "Dev Containers" extension
    (``ms-vscode-remote.remote-containers``), and (3)
    ``"dev.containers.dockerPath": "podman"`` in the user ``settings.json``.

    Returns one ``(status, label, detail)`` line per sub-check.  *config_home*
    is the XDG config base (defaults to ``$XDG_CONFIG_HOME`` / ``~/.config``);
    injectable so tests can point ``settings.json`` at a tmp dir.

    NOTE (Phase-0 UNKNOWN): rootless podman may ALSO require the user podman
    socket (``systemctl --user --now enable podman.socket``) plus a matching
    ``dev.containers.dockerHost``.  Whether ``dockerPath: podman`` alone
    suffices is unconfirmed, so a socket sub-check is deliberately NOT added
    here yet; it may be added once Phase-0 settles it.
    """
    import subprocess

    results: list[tuple[str, str, str]] = []

    # 1. `code` CLI on PATH.
    code_bin = shutil.which("code")
    if code_bin is None:
        results.append(
            (
                "!!",
                "VS Code CLI",
                "'code' not on PATH -- install VS Code and run Command Palette "
                "-> 'Shell Command: Install code command in PATH'",
            )
        )
    else:
        results.append(("ok", "VS Code CLI", code_bin))

    # 2. Dev Containers extension -- only checkable via the `code` CLI.
    if code_bin is None:
        results.append(
            ("--", "VS Code Dev Containers ext", "cannot check -- code not on PATH")
        )
    else:
        try:
            proc = subprocess.run(
                [code_bin, "--list-extensions"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception:
            # Catches a raising CLI AND a HANG: a wedged `code` (shell wrapper
            # blocking on a remote, stuck node proc) would otherwise block
            # diagnose forever.  subprocess.TimeoutExpired subclasses Exception,
            # so a timeout routes here to the honest `-- cannot check` line.
            proc = None
        if proc is None or proc.returncode != 0:
            results.append(
                (
                    "--",
                    "VS Code Dev Containers ext",
                    "cannot check -- 'code --list-extensions' failed",
                )
            )
        else:
            installed = {
                line.strip().lower()
                for line in proc.stdout.splitlines()
                if line.strip()
            }
            if _DEVCONTAINERS_EXT_ID.lower() in installed:
                results.append(
                    ("ok", "VS Code Dev Containers ext", _DEVCONTAINERS_EXT_ID)
                )
            else:
                results.append(
                    (
                        "!!",
                        "VS Code Dev Containers ext",
                        "not installed -- run: code --install-extension "
                        f"{_DEVCONTAINERS_EXT_ID}",
                    )
                )

    # 3. dev.containers.dockerPath in the user settings.json.
    if config_home is None:
        from kanibako.settings.paths import xdg

        config_home = xdg("XDG_CONFIG_HOME", ".config")
    settings_path = config_home / "Code" / "User" / "settings.json"
    results.append(_check_vscode_docker_path(settings_path))

    return results


def run_system_diagnose(args: object) -> int:
    """Run full system diagnostics."""
    from kanibako.settings.config import config_file_path, load_config, load_merged_config
    from kanibako.settings.paths import load_std_paths, xdg

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
    except KanibakoError as e:
        _report_settings_error("Image", e)
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
            from kanibako.runtime.container import ContainerRuntime

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
        from kanibako.settings.paths import resolve_system_paths
        data_home = xdg("XDG_DATA_HOME", ".local/share")
        data_path = resolve_system_paths(
            config.config_paths, data_home=data_home, home=Path.home(),
        )["config.data"]
        status, detail = _check_storage(data_path)
        print(_format_check(status, "Storage", detail))
    except Exception:
        print(_format_check("--", "Storage", "cannot check"))

    # Lifecycle journal: surface any lingering in-flight / interrupted ops
    # (create/import/connect) globally.  Normally empty -> a clean PASS line.
    if std is not None:
        for status, detail in _check_journal(std):
            print(_format_check(status, "Journal", detail))
    else:
        print(_format_check("--", "Journal", "cannot check"))

    # VS Code "Attach to Running Container" host prerequisites (feature #1).
    # This is a HOST-side prerequisite (does the operator's machine have the
    # `code` CLI + Dev Containers extension + podman dockerPath?), not per-box
    # state -- so it belongs on system-diagnose only, not run_box_diagnose.
    # Wrapped so any failure degrades to a single `--` line, never crashing.
    try:
        for status, label, detail in _check_vscode():
            print(_format_check(status, label, detail))
    except Exception:
        print(_format_check("--", "VS Code", "cannot check"))

    print()
    return 0


def run_box_diagnose(args: object) -> int:
    """Run diagnostics for a specific project box."""
    from kanibako.settings.config import config_file_path, load_config
    from kanibako.settings.paths import load_std_paths, resolve_any_project, xdg

    config_home = xdg("XDG_CONFIG_HOME", ".config")
    cf = config_file_path(config_home)
    config = load_config(cf)
    std = load_std_paths(config)

    from kanibako.commands.flags import resolve_subject_value
    project_dir = resolve_subject_value(
        getattr(args, "project", None) or getattr(args, "path", None),
        getattr(args, "box", None),
    )
    try:
        proj = resolve_any_project(std, config, project_dir)
    except Exception as e:
        print(f"Error: {e}")
        return 1

    # `resolve_any_project` fabricates a default-mode resolution for ANY
    # existing directory, so a successful return does NOT mean a kanibako
    # project is actually registered at the target.  A real box has a
    # box_resolve identity (registry membership / an in-place standalone
    # settings file); a fabricated default-mode resolution of a plain dir does
    # NOT.  Without this guard, diagnose would report a meaningless
    # `[ok] Project directory` followed by a false `[!!] Shell directory:
    # missing` for moved/copied/plain directories.  (P8a: replaces the
    # transitional `read_project_meta(...) is not None` registration signal.)
    from kanibako.launch import box_resolve
    is_registered = (
        proj.project_path is not None
        and box_resolve.resolve_box_identity(proj.project_path, std, config)
        is not None
    )
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

    # Lifecycle journal (this box only): a pending create/import/connect entry
    # for THIS box's host-side dir is an interrupted op needing attention.  The
    # key is the dir CONTAINING home/ (Path(shell_path).parent), the uniform
    # J1/J2 key scheme.  Normally absent -> a clean PASS line.
    if proj.shell_path is not None:
        box_key = str(Path(proj.shell_path).parent)
        for status, detail in _check_journal(std, box_key=box_key):
            print(_format_check(status, "Journal", detail))

    print()
    return 0


def run_rig_diagnose(args: object) -> int:
    """Run diagnostics for rig/image status."""
    from kanibako.settings.config import config_file_path, load_merged_config
    from kanibako.settings.paths import xdg

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
    except KanibakoError as e:
        _report_settings_error("Configured image", e)
    except Exception:
        print(_format_check("--", "Configured image", "cannot check"))

    # List local images
    try:
        from kanibako.runtime.container import ContainerRuntime

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
    ``--skip PKG`` (default = the single configured ``box_image``).  Reuses
    :func:`probe_missing_executables` so a single ephemeral container checks all
    baseline executables per image.
    """
    from kanibako.runtime import baseline as baseline_mod
    from kanibako.settings.config import config_file_path, load_merged_config
    from kanibako.runtime.container import ContainerRuntime
    from kanibako.settings.paths import xdg

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
        except KanibakoError as e:
            _report_settings_error("  Baseline", e)
            return
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

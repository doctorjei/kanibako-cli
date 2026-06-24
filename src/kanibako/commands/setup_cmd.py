"""kanibako setup: interactive setup wizard for first-time configuration."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kanibako.errors import ConfigError


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Register ``setup``'s arguments on *parser*.

    The local ``--agent`` flag selects the default agent non-interactively
    (W1 Phase B).  The blanket parent-parser ``--agent`` is Phase D — this is
    the interim setup-local flag; Phase D reconciles the two.
    """
    parser.add_argument(
        "--agent",
        metavar="NAME",
        default=None,
        help=(
            "Set the default agent to NAME non-interactively (skips the menu). "
            "NAME must be an installed agent plugin (see `kanibako agent list`)."
        ),
    )


def _detected_agents() -> list[tuple[str, str]]:
    """Return ``(name, display_name)`` for every DETECTED (installed) agent.

    Detection mirrors Step 2's report: an agent counts when its host binary is
    found via ``detect()``.  Returned in sorted-by-name order for a stable menu.
    """
    from kanibako.targets import discover_targets

    found: list[tuple[str, str]] = []
    for name, cls in sorted(discover_targets().items()):
        try:
            instance = cls()
            if instance.detect() is not None:
                found.append((name, instance.display_name))
        except Exception:
            continue
    return found


def _known_target_names() -> list[str]:
    """Return the names of every installed agent plugin (detected or not)."""
    from kanibako.targets import discover_targets

    return sorted(discover_targets().keys())


def _settings_paths() -> tuple[Path, Path]:
    """Resolve ``(config_file, system_settings_file)`` for programmatic writes.

    ``config_file`` = ``~/.config/kanibako.yaml`` (holds ``[system]`` values like
    ``setup_completed``).  ``system_settings_file`` = ``@system.settings`` =
    ``global/settings.yaml`` (holds the ``default_agent`` SETTING, where
    ``read_default_agent`` reads it back).
    """
    from kanibako.config import config_file_path, load_config
    from kanibako.paths import load_std_paths, xdg

    config_home = xdg("XDG_CONFIG_HOME", ".config")
    cf = config_file_path(config_home)
    std = load_std_paths(load_config(cf))
    return cf, std.settings


def _write_default_agent(name: str) -> None:
    """Programmatically write the ``default_agent`` SETTING.

    Bypasses the file-only CLI guard (Phase A): writes ``agent.default``'s
    ``default_agent`` leaf into the system settings file via the same preserving
    low-level path ``set_config_value`` uses for agent settings, so it round-trips
    through ``read_default_agent``.
    """
    from kanibako.config_interface import _write_nested_toml_key

    _, ssp = _settings_paths()
    ssp.parent.mkdir(parents=True, exist_ok=True)
    _write_nested_toml_key(ssp, ("agent", "default"), "default_agent", name)


def _write_setup_marker() -> None:
    """Write ``system.setup_completed = __version__`` to the config file."""
    from kanibako import __version__
    from kanibako.config_interface import write_system_value

    cf, _ = _settings_paths()
    cf.parent.mkdir(parents=True, exist_ok=True)
    write_system_value(cf, "setup_completed", __version__)


def _select_agent_interactive(detected: list[tuple[str, str]]) -> str | None:
    """Prompt the user to pick an agent from *detected*; return the name or None.

    Presents a numbered menu of detected agents plus a "skip" option.  With 2+
    agents, skip is GATED behind an explicit ``y``/``yes`` confirm (a naked
    ``launch`` would otherwise fail); anything else re-prompts the choice.  With
    exactly 1 agent, skip is harmless and accepted silently.  Returns the chosen
    agent name, or ``None`` to skip.
    """
    skip_index = len(detected) + 1
    while True:
        print("  Select a default agent:")
        for i, (_, display) in enumerate(detected, start=1):
            print(f"    {i}) {display}")
        print(f"    {skip_index}) Skip (set a default later)")
        try:
            raw = input("  Enter a number: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None

        try:
            choice = int(raw)
        except ValueError:
            print("  Not a number; please try again.")
            print()
            continue

        if 1 <= choice <= len(detected):
            return detected[choice - 1][0]

        if choice == skip_index:
            if len(detected) >= 2:
                print()
                print(
                    "  Warning: with 2+ agents installed and no default, a bare "
                    "`kanibako launch`/`start` will FAIL."
                )
                print(
                    "  You must pass `--agent <name>` or set a default "
                    "(re-run `kanibako setup`)."
                )
                try:
                    confirm = input("  Skip anyway? [y/N] ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    print()
                    return None
                if confirm in ("y", "yes"):
                    return None
                # Not confirmed → re-prompt the choice.
                print()
                continue
            # Exactly 1 agent → skip is harmless.
            return None

        print("  Out of range; please try again.")
        print()


def run_setup(args: argparse.Namespace) -> int:
    """Run the interactive setup wizard."""
    print()
    print("Kanibako Setup")
    print("=" * 40)
    print()

    # Step 1: Container runtime
    print("Step 1: Container Runtime")
    from kanibako.commands.diagnose import _check_runtime

    status, detail = _check_runtime()
    if status == "ok":
        print(f"  [ok] {detail}")
    else:
        print("  [!!] No container runtime found.")
        print("       Install podman (https://podman.io/) or Docker.")
        print()
        return 1
    print()

    # Step 2: Detect agents
    print("Step 2: Agent Detection")
    from kanibako.targets import discover_targets

    targets = discover_targets()
    found_any = False
    for name, cls in targets.items():
        try:
            instance = cls()
            if not getattr(instance, "has_binary", True):
                # The binary-less "Shell" no-agent target needs no host binary;
                # it ships in the image and is always available -- never flag it
                # as "not found" (mirrors `system diagnose`).
                print(
                    f"  [ok] {instance.display_name} "
                    "(image default; no host binary needed)"
                )
                found_any = True
                continue
            install = instance.detect()
            if install is not None:
                print(f"  [ok] {instance.display_name} detected")
                found_any = True
            else:
                print(f"  [--] {instance.display_name} not found on this system")
        except Exception:
            print(f"  [--] {name}: error during detection")

    if not targets:
        print("  [!!] No agent plugins installed.")
        print("       Install one: pip install kanibako-agent-claude")
    elif not found_any:
        print()
        print("  No agents detected on this system.")
        print("  Install an agent (e.g., Claude Code) and try again.")
    print()

    # Step 3: Default image
    print("Step 3: Container Rig")
    from kanibako.commands.diagnose import _check_image

    try:
        from kanibako.config import config_file_path, load_merged_config
        from kanibako.paths import xdg

        config_home = xdg("XDG_CONFIG_HOME", ".config")
        cf = config_file_path(config_home)
        merged = load_merged_config(cf, None)
        status, detail = _check_image(merged)
        if status == "ok":
            print(f"  [ok] {detail}")
        else:
            print(f"  [--] {detail}")
            print("       The rig will be pulled automatically on first use.")
    except Exception:
        print("  [--] Cannot check (configuration not initialized yet)")
        print("       Rigs will be pulled automatically on first use.")
    print()

    # Step 4: Default agent selection (the ONLY interactive place in the CLI).
    print("Step 4: Default Agent")
    selected = _run_agent_selection(args)
    print()

    # Mark setup complete (always — a graceful non-TTY skip still counts as a
    # successful setup run).  Records the running build's version string.
    try:
        _write_setup_marker()
    except Exception as e:  # pragma: no cover - defensive
        print(f"  [!!] Could not record setup completion: {e}", file=sys.stderr)

    # Summary
    print("Setup Complete")
    print("-" * 40)
    if selected is not None:
        print(f"  Default agent set to '{selected}'.")
    if found_any:
        print("  You're ready to go! Run `kanibako` in any project directory.")
    else:
        print("  Install an agent plugin and its host binary, then run `kanibako`.")
    print()
    print("  For a full health check: kanibako system diagnose")
    print()

    return 0


def _run_agent_selection(args: argparse.Namespace) -> str | None:
    """Handle agent selection; write the default on success. Returns the pick.

    Returns the selected agent name when one is written, else ``None`` (skip /
    non-TTY / no flag).  Never writes a literal ``"none"``.
    """
    requested = getattr(args, "agent", None)

    # Non-interactive: `setup --agent <name>`.
    if requested:
        if requested not in _known_target_names():
            available = ", ".join(_known_target_names()) or "(none installed)"
            # Hard error: an unknown agent must NOT be treated as a graceful
            # skip — return non-zero and write NEITHER the default NOR the
            # setup-completion marker.  Raising a KanibakoError aborts
            # ``run_setup`` BEFORE the marker write; cli.py surfaces the
            # message verbatim with a non-zero exit.
            raise ConfigError(
                f"Unknown agent '{requested}'. Installed agents: {available}.\n"
                "Install the plugin (e.g. pip install kanibako-agent-"
                f"{requested}) or pick from the list above."
            )
        _write_default_agent(requested)
        print(f"  [ok] Default agent set to '{requested}'.")
        return requested

    detected = _detected_agents()

    # Non-TTY without --agent: never prompt; skip gracefully.
    if not sys.stdin.isatty():
        print("  [--] No agent selected (non-interactive).")
        print(
            "       Set a default later with `kanibako setup` or pass "
            "`--agent <name>` per command."
        )
        return None

    if not detected:
        print("  [--] No agents detected; nothing to select.")
        print("       Install an agent plugin, then re-run `kanibako setup`.")
        return None

    chosen = _select_agent_interactive(detected)
    if chosen is None:
        print("  [--] No default agent set.")
        return None
    _write_default_agent(chosen)
    print(f"  [ok] Default agent set to '{chosen}'.")
    return chosen

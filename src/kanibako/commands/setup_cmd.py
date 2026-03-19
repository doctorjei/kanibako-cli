"""kanibako setup: interactive setup wizard for first-time configuration."""

from __future__ import annotations

import argparse


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
    print("Step 3: Container Image")
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
            print("       The image will be pulled automatically on first use.")
    except Exception:
        print("  [--] Cannot check (configuration not initialized yet)")
        print("       Images will be pulled automatically on first use.")
    print()

    # Summary
    print("Setup Complete")
    print("-" * 40)
    if found_any:
        print("  You're ready to go! Run `kanibako` in any project directory.")
    else:
        print("  Install an agent plugin and its host binary, then run `kanibako`.")
    print()
    print("  For a full health check: kanibako system diagnose")
    print()

    return 0

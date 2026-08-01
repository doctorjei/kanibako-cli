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
    parser.add_argument(
        "--refresh-templates",
        action="store_true",
        help=(
            "Force-accept the template refresh non-interactively (headless path "
            "out of the template-staleness gate): overwrite shipped template "
            "files with their current packaged versions. The flag is itself the "
            "informed consent — your OWN files are untouched, but edits you made "
            "to SHIPPED files are replaced."
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

    ``config_file`` = ``~/.config/kanibako_config.yaml`` (holds ``[system]`` values like
    ``setup_completed``).  ``system_settings_file`` = ``@config.settings`` =
    ``global/settings.yaml`` (holds the ``system.agent`` SETTING, where
    ``read_system_agent`` reads it back).
    """
    from kanibako.config import config_file_path, load_config
    from kanibako.paths import load_std_paths, xdg

    config_home = xdg("XDG_CONFIG_HOME", ".config")
    cf = config_file_path(config_home)
    std = load_std_paths(load_config(cf))
    return cf, std.settings


def _write_system_agent(name: str) -> None:
    """Programmatically write the ``system.agent`` SETTING (spec §2g L1187).

    Writes the ``system:`` table's ``agent`` leaf into the system settings file via
    the same preserving low-level path ``set_config_value`` uses, so it round-trips
    through ``config.read_system_agent`` AND is read by the ordinary system tier of
    the settings cascade.

    ⮕ P7: was ``_write_default_agent``, writing ``agent.default``'s
    ``default_agent`` leaf — a location that made the stored default an undeclared
    key riding the AGENT tier. Migration M-4 (documentation only).
    """
    from kanibako.config_interface import _write_nested_toml_key

    _, ssp = _settings_paths()
    ssp.parent.mkdir(parents=True, exist_ok=True)
    _write_nested_toml_key(ssp, ("system",), "agent", name)


def _write_setup_marker() -> None:
    """Write ``system.setup_completed = __version__`` to the config file."""
    from kanibako import __version__
    from kanibako.config_interface import write_system_value

    cf, _ = _settings_paths()
    cf.parent.mkdir(parents=True, exist_ok=True)
    write_system_value(cf, "setup_completed", __version__)


def _write_templates_stamp(names: list[str]) -> None:
    """Write ``system.templates_stamp`` = the current packaged-template digest.

    Recording the stamp is what CLEARS the hard template-staleness gate — done
    after a refresh is applied, when nothing is stale, or on an informed decline.
    """
    from kanibako.config_interface import write_system_value
    from kanibako.launch.templates import packaged_templates_digest

    cf, _ = _settings_paths()
    cf.parent.mkdir(parents=True, exist_ok=True)
    write_system_value(cf, "templates_stamp", packaged_templates_digest(names))


def _run_template_refresh(args: argparse.Namespace) -> None:
    """Template-update step: refresh shipped templates + stamp (informed consent).

    Branches (ratified brief):

    * ``--refresh-templates`` forced flag → apply refresh + stamp (the flag IS
      the consent), one-line summary.
    * nothing to add AND nothing to overwrite → stamp silently (clears the gate).
    * TTY → warn, show the add/overwrite plan + reassurance + peril, prompt:
      accept → apply + stamp; decline → STAMP ANYWAY (informed choice; the gate
      clears) but leave files as-is.
    * non-TTY, no flag → SKIP WITHOUT stamping (no informed choice possible → the
      hard gate keeps erroring), point at interactive setup / ``--refresh-templates``.

    ⚑ This step is J-6's **B-action** (TEMPLATE UPDATE) and its A-action trigger in
    one place. The refresh reaches the system-owned packaged STAGING only, so it
    changes what FUTURE instantiations get and never rewrites an existing store; the
    ``kept`` list reports the user-owned files whose packaged version moved on while
    their copy stayed. It is also the DELIBERATE trigger of the agent-store
    materialisation (``ensure_agent_stores``, run inside
    ``install_packaged_templates``), which is where a newly pip-installed plugin
    finally gets its store — pip installs run no code, so "at plugin install" means
    "at the next trigger", and the staleness gate is what forces this one.
    """
    from kanibako.config import load_config
    from kanibako.paths import load_std_paths
    from kanibako.launch.templates import install_packaged_templates, plan_template_refresh

    cf, _ = _settings_paths()
    std = load_std_paths(load_config(cf))
    names = _known_target_names()

    added, overwritten, kept = plan_template_refresh(std, names)
    forced = bool(getattr(args, "refresh_templates", False))

    print("Step 5: Templates")

    def _report_kept() -> None:
        if not kept:
            return
        print(f"  Kept YOUR copy ({len(kept)} file(s) differ from the shipped one):")
        for path in kept:
            print(f"    = {path}")

    if forced:
        install_packaged_templates(std, names, refresh=True)
        _write_templates_stamp(names)
        print(
            f"  [ok] Templates refreshed "
            f"({len(added)} added, {len(overwritten)} updated)."
        )
        _report_kept()
        return

    if not added and not overwritten and not kept:
        # Already current: clear the gate silently, no prompt.
        _write_templates_stamp(names)
        print("  [ok] Templates are up to date.")
        return

    if sys.stdin.isatty():
        print("  Your template store is out of date with this kanibako build.")
        if added:
            print(f"  Files to ADD ({len(added)}):")
            for path in added:
                print(f"    + {path}")
        if overwritten:
            print(f"  Files to UPDATE ({len(overwritten)}):")
            for path in overwritten:
                print(f"    ~ {path}")
        _report_kept()
        print("  Your OWN template files are untouched.")
        print("  Edits you made to SHIPPED files will be replaced.")
        try:
            answer = input("  Update templates now? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            answer = ""
        if answer in ("y", "yes"):
            install_packaged_templates(std, names, refresh=True)
            _write_templates_stamp(names)
            print("  [ok] Templates refreshed.")
        else:
            # Informed decline: STAMP ANYWAY so the gate clears; files unchanged.
            _write_templates_stamp(names)
            print(
                "  [--] Declining leaves your template store out of date — an "
                "unblessed state you're choosing knowingly. Re-run "
                "`kanibako setup` anytime to update."
            )
        return

    # Non-TTY, no forced flag: no informed choice possible → skip WITHOUT
    # stamping, so the hard staleness gate keeps erroring until the user runs an
    # interactive setup or passes --refresh-templates.
    print(
        "  [--] Templates are out of date but cannot be updated non-"
        "interactively."
    )
    print(
        "       Re-run `kanibako setup` in a terminal, or pass "
        "`--refresh-templates`."
    )


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

    # Step 5: Template refresh (TRUE REFRESH; clears the staleness gate).
    _run_template_refresh(args)
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
        _write_system_agent(requested)
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
    _write_system_agent(chosen)
    print(f"  [ok] Default agent set to '{chosen}'.")
    return chosen

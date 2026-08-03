"""kanibako setup: interactive setup wizard for first-time configuration."""

from __future__ import annotations

import argparse
import sys
from enum import Enum
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
            "Force-accept the template refresh non-interactively (the headless "
            "path — the prompt needs a terminal): overwrite shipped template "
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
    from kanibako.settings.config import config_file_path, load_config
    from kanibako.settings.paths import load_std_paths, xdg

    config_home = xdg("XDG_CONFIG_HOME", ".config")
    cf = config_file_path(config_home)
    std = load_std_paths(load_config(cf))
    return cf, std.settings


def _write_system_agent(name: str) -> None:
    """Programmatically write the ``system.agent`` SETTING (spec §2g).

    Writes the ``system:`` table's ``agent`` leaf into the system settings file via
    the same preserving low-level path ``set_config_value`` uses, so it round-trips
    through ``config.read_system_agent`` AND is read by the ordinary system tier of
    the settings cascade.

    ⮕ P7: was ``_write_default_agent``, writing ``agent.default``'s
    ``default_agent`` leaf — a location that made the stored default an undeclared
    key riding the AGENT tier. Migration M-4 (documentation only).
    """
    from kanibako.settings.config_io import write_nested_key

    _, ssp = _settings_paths()
    ssp.parent.mkdir(parents=True, exist_ok=True)
    write_nested_key(ssp, ("system",), "agent", name)


def _write_setup_marker() -> None:
    """Write ``system.setup_completed = __version__`` to the config file."""
    from kanibako import __version__
    from kanibako.settings.config_interface import write_system_value

    cf, _ = _settings_paths()
    cf.parent.mkdir(parents=True, exist_ok=True)
    write_system_value(cf, "setup_completed", __version__)


class TemplateStep(Enum):
    """What Step 5 actually did — and therefore whether setup may claim completion.

    The distinction that matters is NOT "were files copied" but "did this run reach
    a settled state the user either got or knowingly chose".  Three of the four
    outcomes do; :attr:`SKIPPED` does not, because no informed choice is possible
    without a terminal.  :attr:`records_completion` is the one place that mapping
    lives, and it names the recording outcomes POSITIVELY: a new member added here
    withholds the marker until it is listed there, so the default is the safe
    direction and the guard test (``test_only_skipped_outcome_withholds_completion``)
    fails until the decision is made explicitly.
    """

    #: Packaged templates were copied into the system-owned staging.
    REFRESHED = "refreshed"
    #: Nothing to add, nothing to overwrite, nothing kept — already current.
    CURRENT = "current"
    #: The user was asked at a terminal and said no.  An INFORMED choice.
    DECLINED = "declined"
    #: Non-TTY with no ``--refresh-templates``: no informed choice was possible,
    #: so the store was left stale WITHOUT the user ever being asked.
    SKIPPED = "skipped"

    @property
    def records_completion(self) -> bool:
        """Whether :func:`run_setup` may write the setup-completion marker.

        ⚑ False for :attr:`SKIPPED` ONLY.  Withholding the marker is what keeps
        the ``setup_compat_gate`` BCV block erroring on an unmigrated store — the
        backstop that makes "a new box seeds empty" unreachable WITHOUT an informed
        decline.  (A decline reaches a byte-identical on-disk state by the user's
        own knowing choice, and withholding is a no-op when the stored marker
        already clears the gate.)  Recording completion for a run that silently
        skipped the template step would clear that block against a store setup
        never touched.

        ⚑ The test is POSITIVE membership, not ``is not SKIPPED``: a negative test
        makes every FUTURE member record completion by default, which is the unsafe
        direction — a later ``FAILED`` arm would clear the BCV block with a green
        suite.  Listing the recording outcomes means a new member withholds until
        someone adds it here deliberately.
        """
        return self in {
            TemplateStep.REFRESHED,
            TemplateStep.CURRENT,
            TemplateStep.DECLINED,
        }


def _run_template_refresh(args: argparse.Namespace) -> TemplateStep:
    """Template-update step: refresh shipped templates under informed consent.

    ⚑ The ``system.templates_stamp`` write that every branch below used to make is
    RETIRED (R-38): there is no template-staleness gate left to clear, so this step
    now only ever COPIES (or declines to copy) files.  The drift announcement moved
    to the setup bands (``SETUP_FCV``/``SETUP_BCV``); migration record M-23.

    Branches (ratified brief, minus the stamping) and the :class:`TemplateStep`
    each returns:

    * ``--refresh-templates`` forced flag → apply the refresh (the flag IS the
      consent), one-line summary → ``REFRESHED``.
    * nothing to add AND nothing to overwrite AND nothing kept → say so, no prompt
      → ``CURRENT``.
    * TTY → warn, show the add/overwrite plan + reassurance + peril, prompt:
      accept → apply (``REFRESHED``); decline → leave files as-is (``DECLINED``).
    * non-TTY, no flag → SKIP (no informed choice is possible without a terminal),
      point at interactive setup / ``--refresh-templates`` → ``SKIPPED``.

    ⚑ The returned outcome GATES the setup-completion marker in :func:`run_setup`
    (see :attr:`TemplateStep.records_completion`).  R-38 retired the per-branch
    stamp but left the completion marker unconditional, which made the non-TTY skip
    sticky: it cleared the BCV hard block against a store the run never migrated.
    Withholding the marker on ``SKIPPED`` restores the pre-R-38 invariant exactly —
    the informed decline still records, only the un-asked skip does not.

    ⚑ This step is J-6's **B-action** (TEMPLATE UPDATE) and its A-action trigger in
    one place. The refresh reaches the system-owned packaged STAGING only, so it
    changes what FUTURE instantiations get and never rewrites an existing store; the
    ``kept`` list reports the user-owned files whose packaged version moved on while
    their copy stayed. It is also the DELIBERATE trigger of the agent-store
    materialisation (``ensure_agent_stores``, run inside
    ``install_packaged_templates``), which is where a newly pip-installed plugin
    finally gets its store — pip installs run no code, so "at plugin install" means
    "at the next trigger". ⚑ Since R-38 nothing FORCES that trigger within a version:
    running ``kanibako setup`` after installing a plugin is now the user's move (the
    ruled accepted loss; M-18's "the staleness gate forces it" no longer holds).
    """
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths
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
        print(
            f"  [ok] Templates refreshed "
            f"({len(added)} added, {len(overwritten)} updated)."
        )
        _report_kept()
        return TemplateStep.REFRESHED

    if not added and not overwritten and not kept:
        # Already current: nothing to copy and nothing to ask about.  A run with
        # nothing to do is still a COMPLETE run.
        print("  [ok] Templates are up to date.")
        return TemplateStep.CURRENT

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
            print("  [ok] Templates refreshed.")
            return TemplateStep.REFRESHED
        # Informed decline: files unchanged, and (since R-38) no template stamp is
        # recorded either — the next setup run simply asks again.  The user WAS
        # asked, so the run still counts as complete (pre-R-38 stamped here too).
        print(
            "  [--] Declining leaves your template store out of date — an "
            "unblessed state you're choosing knowingly. Re-run "
            "`kanibako setup` anytime to update."
        )
        return TemplateStep.DECLINED

    # Non-TTY, no forced flag: no informed choice is possible → skip, leaving the
    # store as-is until the user runs an interactive setup or passes
    # --refresh-templates.  ⚑ This is the ONE outcome that withholds the
    # setup-completion marker, so the skip cannot become sticky.
    print(
        "  [--] Templates are out of date but cannot be updated non-"
        "interactively."
    )
    print(
        "       Re-run `kanibako setup` in a terminal, or pass "
        "`--refresh-templates`."
    )
    return TemplateStep.SKIPPED


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
    """Run the interactive setup wizard.

    Returns ``0`` only when the run recorded the setup-completion marker.  A run
    that withheld it — the non-TTY template SKIP, or a marker write that raised —
    returns ``1``, the same refusal code every other blocked path in this CLI uses
    (``ConfigError`` → rc 1 in :func:`kanibako.cli.main`, and Step 1's missing
    runtime below).  Exiting 0 while printing "Setup Incomplete" would let
    ``kanibako setup && kanibako start`` proceed and ``kanibako setup || exit 1``
    report a success that did not happen — the automation-facing form of exactly
    the false claim the marker gate exists to remove.
    """
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
        from kanibako.settings.config import config_file_path, load_merged_config
        from kanibako.settings.paths import xdg

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

    # Step 5: Template refresh (TRUE REFRESH of the system-owned staging).
    template_step = _run_template_refresh(args)
    print()

    # Mark setup complete — but ONLY when Step 5 reached a settled state (see
    # ``TemplateStep.records_completion``).  A non-TTY skip could not ask, did not
    # refresh, and must therefore record NOTHING: the withheld marker is what keeps
    # ``setup_compat_gate``'s BCV hard block erroring on an unmigrated store.
    # Records the running build's version string.
    recorded = template_step.records_completion
    write_failed = False
    if recorded:
        try:
            _write_setup_marker()
        except Exception as e:
            print(f"  [!!] Could not record setup completion: {e}", file=sys.stderr)
            # The write IS the recording: if it raised, nothing was stored, and
            # this run is incomplete exactly as a non-TTY skip is.  Leaving
            # ``recorded`` true would print "Setup Complete" over a failed write
            # and exit 0 — the banner would claim the very thing that just failed.
            recorded = False
            write_failed = True

    # Summary.  ⚑ The banner must not claim a completion that was not recorded.
    print("Setup Complete" if recorded else "Setup Incomplete")
    print("-" * 40)
    if selected is not None:
        print(f"  Default agent set to '{selected}'.")
    if write_failed:
        # Distinct cause, distinct cure: the template step DID settle, so the
        # terminal/`--refresh-templates` advice below would be simply wrong here.
        print("  Setup completion was NOT recorded: writing the marker failed")
        print("  (the error is on stderr above). Fix that, then re-run")
        print("  `kanibako setup`.")
    elif not recorded:
        print("  Setup completion was NOT recorded: the template step (Step 5)")
        print("  needs an informed choice it cannot get without a terminal.")
        print(
            "  Re-run `kanibako setup` in a terminal, or pass "
            "`--refresh-templates`."
        )
    elif found_any:
        print("  You're ready to go! Run `kanibako` in any project directory.")
    else:
        print("  Install an agent plugin and its host binary, then run `kanibako`.")
    print()
    print("  For a full health check: kanibako system diagnose")
    print()

    # ⚑ The exit status carries the same claim the banner does: a run that
    # recorded nothing is a FAILED setup for anything scripting it.
    return 0 if recorded else 1


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

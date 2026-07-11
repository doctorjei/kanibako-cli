"""E2E tests for the interactive (PTY) box-lifecycle paths.

The stub-claude error-recovery e2e (``test_error_recovery.py``) runs
``kanibako start`` in a NON-tty subprocess, so ``sys.stdin.isatty()`` is False
and kanibako takes the non-interactive (captured-logs) branch.  These tests
launch a box UNDER A REAL PTY (pexpect) so ``isatty()`` is True and the genuine
interactive ``tmux attach`` passthrough runs.  Two lifecycle branches are
covered:

  - :class:`TestInteractiveAttachOnDeath` -- the agent crashes on launch; the
    death marker must surface and the raw podman error must NOT leak (the
    "detach -> teardown" half of the two-state lifecycle), and
  - :class:`TestInteractiveDetach` -- the agent stays alive; a ``Ctrl-b d``
    detach must KEEP the box running, and a reattach must hit the SAME
    container, not recreate it (the "detach -> keep" half).

They use the TESTING-ONLY ``DeadTarget`` / ``LiveTarget`` directory-plugins
(``dead.py`` / ``live.py``) plus their ``dead-agent`` / ``live-agent`` scripts,
all of which live under ``tests/`` only and are never packaged.  Requires
podman + tmux + the e2e image, and pexpect; they SKIP gracefully when any is
absent.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

# Graceful skip when pexpect is not installed (it is a dev-only test dep).
pexpect = pytest.importorskip("pexpect")

from tests.e2e.conftest import (  # noqa: E402
    E2E_IMAGE,
    e2e_requires,
    run_kanibako,
)

pytestmark = [pytest.mark.e2e, *e2e_requires]

# Fixtures live alongside this test under tests/ only — never packaged.
_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "dead-agent"
_PLUGIN_SRC = _FIXTURE_DIR / "dead.py"
_DEAD_EXE_SRC = _FIXTURE_DIR / "dead-agent"

_LIVE_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "live-agent"
_LIVE_PLUGIN_SRC = _LIVE_FIXTURE_DIR / "live.py"
_LIVE_EXE_SRC = _LIVE_FIXTURE_DIR / "live-agent"

# The live box's container name.  A `kanibako create --name live-box` box is
# named `kanibako-<name>`, NOT the `kanibako-e2e-` prefix the suite-wide e2e
# teardown matches, so the live_env teardown removes this name explicitly.
_LIVE_BOX_NAME = "kanibako-live-box"

# Budget for the interactive `kanibako start` under a PTY.  Modeled on the
# prototype probe (90s): covers a cold first container start (fuse-overlayfs
# first mount + userns/cgroup setup) with headroom while still failing fast.
START_TIMEOUT = 90

# Budget for the tmux session to come up (marker / status line) once the
# container is started.  Modeled on the prototype probe (60s).
SESSION_UP_TIMEOUT = 60


@pytest.fixture()
def dead_env(tmp_path, host_storage_conf) -> dict:
    """Isolated e2e environment whose default agent crashes on launch.

    Mirrors :func:`e2e_env` (isolated HOME / XDG dirs, image pinned via
    ``kanibako_config.yaml``, podman storage pinned via the reused ``host_storage_conf``
    fixture) but targets the TESTING-ONLY ``DeadTarget`` instead of claude:

      - the ``dead-agent`` crash script is installed at
        ``<HOME>/.local/bin/dead-agent`` (the per-agent contract path that
        ``DeadTarget.detect()`` anchors to), chmod 0755,
      - ``dead.py`` is dropped into
        ``<XDG_DATA_HOME>/kanibako/plugins/dead.py`` (the user directory-plugin
        tier) so the plugin is discovered, and
      - system settings pin ``agent.default.default_agent: dead`` so
        ``resolve_agent`` selects it unambiguously.

    CONTAINERS_CONF is intentionally NOT set: the test env injects netns=host
    out-of-band, so the fixture stays env-agnostic and just inherits os.environ
    like ``e2e_env`` does.
    """
    home = tmp_path / "home"
    config_home = tmp_path / "config"
    data_home = tmp_path / "data"
    state_home = tmp_path / "state"
    cache_home = tmp_path / "cache"
    project = tmp_path / "project"

    for d in (home, config_home, data_home, state_home, cache_home, project):
        d.mkdir()

    # The dead-agent crash script at the detect() contract path (~/.local/bin).
    dead_bin_dir = home / ".local" / "bin"
    dead_bin_dir.mkdir(parents=True)
    dead_binary = dead_bin_dir / "dead-agent"
    shutil.copy2(_DEAD_EXE_SRC, dead_binary)
    dead_binary.chmod(0o755)

    # The DeadTarget plugin in the user directory-plugin tier.
    plugin_dir = data_home / "kanibako" / "plugins"
    plugin_dir.mkdir(parents=True)
    shutil.copy2(_PLUGIN_SRC, plugin_dir / "dead.py")

    # Pin the e2e image (exactly as e2e_env does).
    kanibako_config = config_home / "kanibako_config.yaml"
    kanibako_config.write_text(
        f'kanibako:\n  image: "{E2E_IMAGE}"\n'
    )
    # Record the template-staleness stamp the way first-run init does — this
    # fixture pre-seeds the config instead of running init, so without the stamp
    # template_staleness_gate hard-errors every `kanibako start` with "bundled
    # templates changed since setup was last run" and no container is created
    # (see e2e_env).  The digest is CONTENT-based over the installed agents'
    # packaged templates; the testing-only dead/live directory-plugins ship no
    # packaged template, so they contribute nothing and the host-computed stamp
    # matches what the subprocess gate recomputes with them discovered.
    from kanibako.config_interface import write_system_value
    from kanibako.targets import discover_targets
    from kanibako.templates import packaged_templates_digest
    write_system_value(
        kanibako_config,
        "templates_stamp",
        packaged_templates_digest(sorted(discover_targets().keys())),
    )

    # System settings: default agent = dead, so resolve_agent picks DeadTarget
    # (mirrors e2e_env's claude pin; the configured-default tier wins in the
    # resolve_agent cascade before the installed-count rule).
    system_settings = data_home / "kanibako" / "global" / "settings.yaml"
    system_settings.parent.mkdir(parents=True, exist_ok=True)
    system_settings.write_text("agent:\n  default:\n    default_agent: dead\n")

    env = os.environ.copy()
    env.update({
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(config_home),
        "XDG_DATA_HOME": str(data_home),
        "XDG_STATE_HOME": str(state_home),
        "XDG_CACHE_HOME": str(cache_home),
        # Put the dead-agent dir first on PATH for parity with e2e_env (detect()
        # anchors to ~/.local/bin, but keep PATH consistent).
        "PATH": f"{dead_bin_dir}:{env.get('PATH', '')}",
        # Pin rootless podman storage to the host's real graphroot (see
        # e2e_env / host_storage_conf).
        "CONTAINERS_STORAGE_CONF": str(host_storage_conf),
    })

    yield {
        "env": env,
        "home": home,
        "project": project,
        "config_home": config_home,
        "data_home": data_home,
        "tmp_path": tmp_path,
    }

    # Teardown: prefix-based cleanup of this test's containers (mirrors e2e_env).
    podman = shutil.which("podman")
    if podman is None:
        return
    import subprocess

    result = subprocess.run(
        [podman, "ps", "-a", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    for name in result.stdout.strip().splitlines():
        name = name.strip()
        if name.startswith("kanibako-e2e-"):
            subprocess.run(
                [podman, "rm", "-f", "-t", "1", name],
                capture_output=True,
                timeout=10,
            )


class TestInteractiveAttachOnDeath:
    """Interactive (PTY) launch of a box whose agent crashes on start."""

    def test_dead_agent_under_pty_shows_marker_not_raw_error(self, dead_env):
        """A PTY-launched box with a crashing agent surfaces the agent marker.

        Under a real PTY the interactive ``tmux attach`` path runs; the agent
        exits non-zero, and kanibako must show the agent's output (marker)
        rather than leaking the raw ``container state improper`` podman error,
        and must itself exit non-zero.
        """
        env = dead_env["env"]
        project = dead_env["project"]

        # Create the box (plain subprocess; no PTY needed).
        result = run_kanibako(
            ["create", str(project), "--name", "dead-box"],
            env=env,
        )
        assert result.returncode == 0, (
            f"create failed: rc={result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

        # Start UNDER A PTY so kanibako sees an interactive terminal and takes
        # the real tmux-attach passthrough.
        child = pexpect.spawn(
            "kanibako",
            ["start", "dead-box"],
            env=env,
            encoding="utf-8",
            dimensions=(40, 120),
            timeout=START_TIMEOUT,
        )
        try:
            child.expect(pexpect.EOF)
        finally:
            child.close()

        out = child.before or ""

        assert "DEAD_AGENT_MARKER" in out, (
            "Expected the dead-agent death marker in the PTY output, but it was "
            f"missing.\nCaptured output:\n{out}"
        )
        assert "container state improper" not in out, (
            "Raw podman 'container state improper' error leaked to the user "
            f"instead of the agent's logs.\nCaptured output:\n{out}"
        )
        assert child.exitstatus not in (0, None), (
            "Expected a non-zero exit status from the crashing launch, got "
            f"exitstatus={child.exitstatus!r} (signalstatus="
            f"{child.signalstatus!r}).\nCaptured output:\n{out}"
        )


@pytest.fixture()
def live_env(tmp_path, host_storage_conf) -> dict:
    """Isolated e2e environment whose default agent stays alive (sleeps).

    Mirrors :func:`dead_env` but targets the TESTING-ONLY ``LiveTarget`` instead
    of the dead agent: the delivered ``live-agent`` script prints a marker and
    then ``exec sleep``, so its tmux session persists and an interactive (PTY)
    test can detach with ``Ctrl-b d`` and verify the box is KEPT running +
    reattachable.

      - the ``live-agent`` script is installed at ``<HOME>/.local/bin/live-agent``
        (the per-agent contract path that ``LiveTarget.detect()`` anchors to),
        chmod 0755,
      - ``live.py`` is dropped into
        ``<XDG_DATA_HOME>/kanibako/plugins/live.py`` (the user directory-plugin
        tier) so the plugin is discovered, and
      - system settings pin ``agent.default.default_agent: live`` so
        ``resolve_agent`` selects it unambiguously.

    CONTAINERS_CONF is intentionally NOT set: the test env injects netns=host
    out-of-band, so the fixture stays env-agnostic and just inherits os.environ
    like ``e2e_env`` does.
    """
    home = tmp_path / "home"
    config_home = tmp_path / "config"
    data_home = tmp_path / "data"
    state_home = tmp_path / "state"
    cache_home = tmp_path / "cache"
    project = tmp_path / "project"

    for d in (home, config_home, data_home, state_home, cache_home, project):
        d.mkdir()

    # The live-agent script at the detect() contract path (~/.local/bin).
    live_bin_dir = home / ".local" / "bin"
    live_bin_dir.mkdir(parents=True)
    live_binary = live_bin_dir / "live-agent"
    shutil.copy2(_LIVE_EXE_SRC, live_binary)
    live_binary.chmod(0o755)

    # The LiveTarget plugin in the user directory-plugin tier.
    plugin_dir = data_home / "kanibako" / "plugins"
    plugin_dir.mkdir(parents=True)
    shutil.copy2(_LIVE_PLUGIN_SRC, plugin_dir / "live.py")

    # Pin the e2e image (exactly as e2e_env / dead_env do).
    kanibako_config = config_home / "kanibako_config.yaml"
    kanibako_config.write_text(
        f'kanibako:\n  image: "{E2E_IMAGE}"\n'
    )
    # Record the template-staleness stamp the way first-run init does — this
    # fixture pre-seeds the config instead of running init, so without the stamp
    # template_staleness_gate hard-errors every `kanibako start` with "bundled
    # templates changed since setup was last run" and no container is created
    # (see e2e_env).  The digest is CONTENT-based over the installed agents'
    # packaged templates; the testing-only dead/live directory-plugins ship no
    # packaged template, so they contribute nothing and the host-computed stamp
    # matches what the subprocess gate recomputes with them discovered.
    from kanibako.config_interface import write_system_value
    from kanibako.targets import discover_targets
    from kanibako.templates import packaged_templates_digest
    write_system_value(
        kanibako_config,
        "templates_stamp",
        packaged_templates_digest(sorted(discover_targets().keys())),
    )

    # System settings: default agent = live, so resolve_agent picks LiveTarget
    # (mirrors dead_env's dead pin; the configured-default tier wins in the
    # resolve_agent cascade before the installed-count rule).
    system_settings = data_home / "kanibako" / "global" / "settings.yaml"
    system_settings.parent.mkdir(parents=True, exist_ok=True)
    system_settings.write_text("agent:\n  default:\n    default_agent: live\n")

    env = os.environ.copy()
    env.update({
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(config_home),
        "XDG_DATA_HOME": str(data_home),
        "XDG_STATE_HOME": str(state_home),
        "XDG_CACHE_HOME": str(cache_home),
        # Put the live-agent dir first on PATH for parity with dead_env (detect()
        # anchors to ~/.local/bin, but keep PATH consistent).
        "PATH": f"{live_bin_dir}:{env.get('PATH', '')}",
        # Pin rootless podman storage to the host's real graphroot (see
        # e2e_env / host_storage_conf).
        "CONTAINERS_STORAGE_CONF": str(host_storage_conf),
    })

    yield {
        "env": env,
        "home": home,
        "project": project,
        "config_home": config_home,
        "data_home": data_home,
        "tmp_path": tmp_path,
    }

    # Teardown: the live box is named `kanibako-live-box` (NOT the
    # `kanibako-e2e-` prefix the suite teardown / dead_env match), so the
    # prefix sweep would leave it running forever.  Remove it explicitly here
    # so the test cleans up after itself even on failure; then also do the
    # prefix sweep for parity with dead_env.
    podman = shutil.which("podman")
    if podman is None:
        return

    subprocess.run(
        [podman, "rm", "-f", "-t", "1", _LIVE_BOX_NAME],
        capture_output=True,
        timeout=20,
    )

    result = subprocess.run(
        [podman, "ps", "-a", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    for name in result.stdout.strip().splitlines():
        name = name.strip()
        if name.startswith("kanibako-e2e-"):
            subprocess.run(
                [podman, "rm", "-f", "-t", "1", name],
                capture_output=True,
                timeout=10,
            )


def _running_containers() -> dict[str, str]:
    """Return ``name -> id`` for every RUNNING container (via ``podman ps``)."""
    podman = shutil.which("podman")
    assert podman is not None, "podman required"
    out = subprocess.run(
        [podman, "ps", "--format", "{{.Names}}\t{{.ID}}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    running: dict[str, str] = {}
    for line in out.stdout.strip().splitlines():
        if "\t" in line:
            name, cid = line.split("\t", 1)
            running[name.strip()] = cid.strip()
    return running


def _attach_then_detach(env: dict) -> tuple[int | None, str]:
    """Launch ``kanibako start live-box`` under a PTY, detach, return (exit, out).

    Waits for the tmux session to be up (the live-agent marker on first attach,
    or the tmux ``[kanibako]`` status line on a reattach where the marker has
    scrolled off), then sends the tmux detach chord (``Ctrl-b d``) and waits for
    EOF.  Modeled on the prototype probe.
    """
    child = pexpect.spawn(
        "kanibako",
        ["start", "live-box"],
        env=env,
        encoding="utf-8",
        dimensions=(40, 120),
        timeout=START_TIMEOUT,
    )
    out = ""
    try:
        # Wait for the session to be up.  On a reattach the marker may have
        # scrolled, so also accept the tmux status indicator.
        child.expect(
            ["LIVE_AGENT_MARKER", r"\[kanibako\]", pexpect.TIMEOUT],
            timeout=SESSION_UP_TIMEOUT,
        )
        out += child.before or ""
        out += child.after if isinstance(child.after, str) else ""
        time.sleep(2)  # let the pane settle before sending the detach chord
        child.sendcontrol("b")  # tmux prefix
        time.sleep(0.3)
        child.send("d")  # detach
        child.expect(pexpect.EOF, timeout=30)
        out += child.before or ""
    finally:
        child.close()
    return child.exitstatus, out


class TestInteractiveDetach:
    """Interactive (PTY) detach + reattach of a box whose agent stays alive."""

    def test_detach_keeps_box_running_and_reattaches_same_container(
        self, live_env
    ):
        """``Ctrl-b d`` keeps the box running; a reattach hits the SAME container.

        Under a real PTY the interactive ``tmux attach`` path runs against a
        live agent.  Detaching with the tmux chord must take the "detach ->
        keep" half of the two-state lifecycle: the box container STAYS running
        (not torn down).  A second ``kanibako start`` must REATTACH to that same
        container (same id still running), not recreate it.
        """
        env = live_env["env"]
        project = live_env["project"]

        # Create the box (plain subprocess; no PTY needed).
        result = run_kanibako(
            ["create", str(project), "--name", "live-box"],
            env=env,
        )
        assert result.returncode == 0, (
            f"create failed: rc={result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

        before = set(_running_containers())

        # First attach under a PTY, then detach with Ctrl-b d.
        exit1, out1 = _attach_then_detach(env)

        # The box must be KEPT running after the detach.
        after1 = _running_containers()
        box = {n: i for n, i in after1.items() if n not in before}
        assert box, (
            "Expected the box container to STILL be running after Ctrl-b d "
            "detach (two-state 'detach -> keep'), but no new running container "
            f"was found.\nrunning after detach: {after1}\n"
            f"first-attach exit={exit1!r}\nCaptured output:\n{out1}"
        )
        box_name = next(iter(box))
        box_id = box[box_name]

        # Reattach under a PTY and detach again; the SAME container must still
        # be running (reattach, not recreate).
        exit2, out2 = _attach_then_detach(env)

        after2 = _running_containers()
        assert box_name in after2 and after2[box_name] == box_id, (
            "Expected the SAME box container to still be running after a "
            "reattach + second detach (reattach, not recreate), but the id "
            f"changed or it is gone.\nbox {box_name}: {box_id} -> "
            f"{after2.get(box_name)!r}\nrunning after reattach: {after2}\n"
            f"reattach exit={exit2!r}\nCaptured output:\n{out2}"
        )

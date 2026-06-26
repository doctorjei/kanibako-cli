"""E2E test for the interactive (PTY) attach-on-a-dying-container path.

The stub-claude error-recovery e2e (``test_error_recovery.py``) runs
``kanibako start`` in a NON-tty subprocess, so ``sys.stdin.isatty()`` is False
and kanibako takes the non-interactive (captured-logs) branch.  This test
launches a box UNDER A REAL PTY (pexpect) so ``isatty()`` is True and the
genuine interactive ``tmux attach`` passthrough runs against a container whose
agent crashes on launch.  It asserts on the raw terminal bytes that:

  - the agent's death marker (``DEAD_AGENT_MARKER``) surfaces to the user,
  - the raw podman ``container state improper`` error does NOT leak, and
  - the process exits non-zero.

It uses the TESTING-ONLY ``DeadTarget`` directory-plugin (``dead.py``) plus the
``dead-agent`` crash script, both of which live under ``tests/`` only and are
never packaged.  Requires podman + tmux + the e2e image, and pexpect; it SKIPs
gracefully when any is absent.
"""

from __future__ import annotations

import os
import shutil
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

# Budget for the interactive `kanibako start` under a PTY.  Modeled on the
# prototype probe (90s): covers a cold first container start (fuse-overlayfs
# first mount + userns/cgroup setup) with headroom while still failing fast.
START_TIMEOUT = 90


@pytest.fixture()
def dead_env(tmp_path, host_storage_conf) -> dict:
    """Isolated e2e environment whose default agent crashes on launch.

    Mirrors :func:`e2e_env` (isolated HOME / XDG dirs, image pinned via
    ``kanibako.yaml``, podman storage pinned via the reused ``host_storage_conf``
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
    kanibako_config = config_home / "kanibako.yaml"
    kanibako_config.write_text(
        f'kanibako:\n  image: "{E2E_IMAGE}"\n'
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

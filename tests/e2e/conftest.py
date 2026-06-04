"""Fixtures for end-to-end tests.

All e2e tests are marked ``@pytest.mark.e2e`` and require:
- A container runtime (podman) on PATH
- The kanibako-oci image available locally
- tmux installed

Run with::

    pytest tests/e2e/ -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

E2E_IMAGE = "kanibako-oci:latest"
CONTAINER_PREFIX = "kanibako-e2e-"
# Registry source used to pre-warm E2E_IMAGE into the pinned store when it is
# not already present there (see ensure_image_in_pinned_store). Overridable so
# the suite can target a different image ref without editing the file.
DEFAULT_E2E_IMAGE_REF = "ghcr.io/doctorjei/kanibako-oci:edge"
E2E_IMAGE_REF = os.environ.get("KANIBAKO_E2E_IMAGE_REF", DEFAULT_E2E_IMAGE_REF)
# Budget for a single kanibako subprocess (start/shell/stop). The image is
# pre-warmed into the pinned store once at session start
# (ensure_image_in_pinned_store), so no individual test should pay a cold
# image pull. 60s leaves comfortable headroom for a genuinely slow container
# start without masking a regression the way the old 120s cold-pull budget did.
SUBPROCESS_TIMEOUT = 60  # seconds

# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------

_podman = shutil.which("podman")
_tmux = shutil.which("tmux")

requires_podman = pytest.mark.skipif(
    _podman is None, reason="podman not found on PATH"
)
requires_tmux = pytest.mark.skipif(
    _tmux is None, reason="tmux not found on PATH"
)


def _image_available() -> bool:
    """Check if the e2e test image is available locally."""
    if _podman is None:
        return False
    result = subprocess.run(
        [_podman, "image", "inspect", E2E_IMAGE],
        capture_output=True,
        timeout=10,
    )
    return result.returncode == 0


def _host_podman_storage() -> tuple[str, str] | None:
    """Return host's (graphRoot, runRoot) from podman info, or None on failure.

    Captured before any HOME/XDG_DATA_HOME overrides so we can pin the test
    subprocess's podman storage back to the host's real location via
    storage.conf. Without this, kanibako-launched podman looks for images
    in the test's tmp dir and never finds them.
    """
    if _podman is None:
        return None
    result = subprocess.run(
        [_podman, "info", "--format",
         "{{.Store.GraphRoot}}\n{{.Store.RunRoot}}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return None
    lines = result.stdout.strip().splitlines()
    if len(lines) != 2:
        return None
    return lines[0], lines[1]


_host_storage = _host_podman_storage()


requires_image = pytest.mark.skipif(
    not _image_available(),
    reason=f"{E2E_IMAGE} not available locally",
)

# Combine all skip conditions for use in pytestmark
e2e_requires = [requires_podman, requires_tmux, requires_image]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def wait_for_container(name: str, timeout: float = 10.0) -> None:
    """Poll until a container is running, or raise TimeoutError."""
    assert _podman is not None, "podman required"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            [_podman, "inspect", "--format", "{{.State.Running}}", name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip() == "true":
            return
        time.sleep(0.3)
    raise TimeoutError(f"Container {name!r} not running after {timeout}s")


def run_kanibako(
    args: list[str],
    env: dict[str, str],
    *,
    timeout: int = SUBPROCESS_TIMEOUT,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run kanibako CLI as a subprocess."""
    return subprocess.run(
        ["kanibako"] + args,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
    )


def podman_exec(
    container_name: str,
    command: list[str],
    *,
    timeout: int = SUBPROCESS_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    """Run a command inside a running container."""
    assert _podman is not None, "podman required"
    return subprocess.run(
        [_podman, "exec", container_name] + command,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def stub_script() -> Path:
    """Return path to the claude stub script."""
    stub = Path(__file__).parent / "fixtures" / "claude-stub"
    assert stub.is_file(), f"Claude stub not found: {stub}"
    assert os.access(stub, os.X_OK), f"Claude stub not executable: {stub}"
    return stub


@pytest.fixture(scope="session")
def host_storage_conf(tmp_path_factory) -> Path:
    """Write a storage.conf pinning rootless podman to the host's real graphroot.

    The per-test env overrides HOME and XDG_DATA_HOME, which would otherwise
    relocate rootless podman's graphroot into the test's tmp dir, hiding the
    host's pulled images. ``rootless_storage_path`` is the rootless-specific
    knob that survives those overrides (plain ``graphroot`` does not).
    """
    assert _host_storage is not None, "podman info failed; cannot pin storage"
    graphroot, runroot = _host_storage
    conf_dir = tmp_path_factory.mktemp("podman-storage")
    conf_path = conf_dir / "storage.conf"
    conf_path.write_text(
        "[storage]\n"
        'driver = "overlay"\n'
        f'runroot = "{runroot}"\n'
        f'rootless_storage_path = "{graphroot}"\n'
    )
    return conf_path


def _diag(message: str) -> None:
    """Emit a storage diagnostic to stderr and the CI step summary.

    Written to ``$GITHUB_STEP_SUMMARY`` when set so the facts are visible in
    the GitHub Actions run summary without needing ``pytest -s``.
    """
    line = f"[e2e-storage] {message}"
    print(line, file=sys.stderr, flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


@pytest.fixture(scope="session", autouse=True)
def ensure_image_in_pinned_store(host_storage_conf) -> None:
    """Guarantee E2E_IMAGE is present in the pinned store before any test runs.

    The per-test subprocesses pin ``rootless_storage_path`` to the host's
    graphroot (host_storage_conf) so they share the host's image store. The
    workflow tags the pulled image into podman's *default* store; in some
    environments (observed on CI at the v1.3.0 rc) the store podman resolves
    under the pinned config does not contain that tag, so the first
    ``kanibako start`` would cold-pull the image *inside* the first test and
    blow SUBPROCESS_TIMEOUT. Pre-warming here -- once, under the same config
    the subprocesses see, on the session clock rather than a per-test budget --
    removes that flake regardless of why the stores diverge. When the pinned
    store already holds the image (the common local/test-vm case where
    pinned == default), this is a no-op beyond the diagnostics.
    """
    if _podman is None:
        return

    pin_env = os.environ.copy()
    pin_env["CONTAINERS_STORAGE_CONF"] = str(host_storage_conf)

    info = subprocess.run(
        [_podman, "info", "--format", "{{.Store.GraphRoot}}"],
        env=pin_env, capture_output=True, text=True, timeout=20,
    )
    pinned_root = info.stdout.strip() if info.returncode == 0 else "<unknown>"
    _diag(f"captured host store: {_host_storage}")
    _diag(f"pinned-config graphroot: {pinned_root!r}")

    present = subprocess.run(
        [_podman, "image", "exists", E2E_IMAGE],
        env=pin_env, capture_output=True, timeout=20,
    )
    if present.returncode == 0:
        _diag(f"{E2E_IMAGE} already present in pinned store -- no pre-warm needed")
        return

    _diag(f"{E2E_IMAGE} MISSING from pinned store -- pre-warming from {E2E_IMAGE_REF}")
    pull = subprocess.run(
        [_podman, "pull", E2E_IMAGE_REF],
        env=pin_env, capture_output=True, text=True, timeout=900,
    )
    if pull.returncode != 0:
        _diag(f"pre-warm pull FAILED: {pull.stderr.strip()[-400:]}")
        return
    subprocess.run(
        [_podman, "tag", E2E_IMAGE_REF, E2E_IMAGE],
        env=pin_env, capture_output=True, timeout=30,
    )
    _diag(f"pre-warm complete: {E2E_IMAGE_REF} -> {E2E_IMAGE}")


@pytest.fixture(scope="session", autouse=True)
def session_cleanup():
    """Safety-net: remove any leftover e2e test containers at suite end."""
    yield
    if _podman is None:
        return
    # List containers with the e2e prefix
    result = subprocess.run(
        [_podman, "ps", "-a", "--filter", f"name={CONTAINER_PREFIX}",
         "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    for name in result.stdout.strip().splitlines():
        name = name.strip()
        if name:
            subprocess.run(
                [_podman, "rm", "-f", name],
                capture_output=True,
                timeout=10,
            )


# ---------------------------------------------------------------------------
# Function-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def e2e_env(tmp_path, stub_script, host_storage_conf) -> dict:
    """Create an isolated test environment for one e2e test.

    Returns a dict with:
      - "env": environment dict for subprocess calls
      - "home": Path to isolated HOME
      - "project": Path to test project directory
      - "config_home", "data_home": Path to XDG dirs
      - "stub_script": Path to the claude stub
      - "tmp_path": Path to test's temp root
    """
    home = tmp_path / "home"
    config_home = tmp_path / "config"
    data_home = tmp_path / "data"
    state_home = tmp_path / "state"
    cache_home = tmp_path / "cache"
    project = tmp_path / "project"

    for d in (home, config_home, data_home, state_home, cache_home, project):
        d.mkdir()

    # Create fake Claude install so ClaudeTarget.detect() finds the stub.
    # detect() calls shutil.which("claude"), so we need it on PATH.
    # It then resolves symlinks and walks up to find a "claude" dir.
    claude_bin_dir = home / ".local" / "bin"
    claude_install_dir = home / ".local" / "share" / "claude"
    claude_bin_dir.mkdir(parents=True)
    claude_install_dir.mkdir(parents=True)

    # Copy stub as the "claude" binary
    claude_binary = claude_bin_dir / "claude"
    shutil.copy2(stub_script, claude_binary)
    claude_binary.chmod(0o755)

    # Seed fake credentials so refresh_credentials() has something to copy
    claude_config_dir = home / ".claude"
    claude_config_dir.mkdir()
    creds = {"claudeAiOauth": {"accessToken": "e2e-test-token"}}
    (claude_config_dir / ".credentials.json").write_text(json.dumps(creds))

    # Build kanibako config
    kanibako_config = config_home / "kanibako.toml"
    kanibako_config.write_text(
        f'[kanibako]\nimage = "{E2E_IMAGE}"\n'
    )

    # Create a name file so container_name_for() gives a predictable name
    # We register via kanibako create later, but for name computation we
    # need the names.toml to exist.
    names_dir = data_home / "kanibako"
    names_dir.mkdir(parents=True)

    # Environment with isolated paths and stub on PATH
    env = os.environ.copy()
    env.update({
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(config_home),
        "XDG_DATA_HOME": str(data_home),
        "XDG_STATE_HOME": str(state_home),
        "XDG_CACHE_HOME": str(cache_home),
        # Put claude stub dir first on PATH so shutil.which("claude") finds it
        "PATH": f"{claude_bin_dir}:{env.get('PATH', '')}",
        # Disable telemetry in test containers
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        # Pin rootless podman storage back to the host's real location.
        # HOME/XDG_DATA_HOME overrides above would otherwise relocate
        # graphroot into tmp_path and hide the host's pulled images.
        "CONTAINERS_STORAGE_CONF": str(host_storage_conf),
    })

    # Compute expected container name.  For a fresh project created via
    # kanibako create, the name is based on the directory name.
    # We'll use "kanibako create" in tests to set up properly.
    # For now, provide a predictable project path.

    yield {
        "env": env,
        "home": home,
        "project": project,
        "config_home": config_home,
        "data_home": data_home,
        "stub_script": stub_script,
        "tmp_path": tmp_path,
    }

    # Teardown: stop and remove any containers from this test.
    # We can't know the exact name without running kanibako, so do a
    # prefix-based cleanup.
    if _podman is None:
        return
    result = subprocess.run(
        [_podman, "ps", "-a", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    for name in result.stdout.strip().splitlines():
        name = name.strip()
        if not name:
            continue
        # Clean up only e2e test containers (not user's real ones)
        if name.startswith("kanibako-e2e-"):
            subprocess.run(
                [_podman, "rm", "-f", "-t", "1", name],
                capture_output=True,
                timeout=10,
            )

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
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

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
# Budget for a single kanibako subprocess (start/shell/stop). The real cost
# the budget must cover is the FIRST container start on a cold runner: CI
# timing (e2e run with --durations) shows the first `kanibako start` ~30s
# (fuse-overlayfs first mount + cgroup/userns setup), with every later start
# ~8-9s once caches are warm. The original 30s budget made that first test
# flaky (this, not a per-test image pull, was the v1.3.0 rc failure); a brief
# bump to 120s over-corrected. 60s covers a cold first start with headroom
# while still failing fast on a real regression. The image is also pre-warmed
# into the pinned store once at session start (ensure_image_in_pinned_store),
# so no test pays an image pull regardless.
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
    """Check if the e2e test image is available locally.

    ⚑ Runs at COLLECTION time (the ``requires_image`` marker below is built at
    module scope), so it must never raise: this module is collected by the plain
    ``pytest tests/`` unit run too, where the e2e tests are all deselected. An
    escaping exception there is a COLLECTION ERROR, which aborts the whole run
    before a single unit test executes — a probe for an optional dependency
    taking the entire suite down with it.
    """
    if _podman is None:
        return False
    try:
        result = subprocess.run(
            [_podman, "image", "inspect", E2E_IMAGE],
            capture_output=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def _host_podman_storage() -> tuple[str, str] | None:
    """Return host's (graphRoot, runRoot) from podman info, or None on failure.

    Captured before any HOME/XDG_DATA_HOME overrides so we can pin the test
    subprocess's podman storage back to the host's real location via
    storage.conf. Without this, kanibako-launched podman looks for images
    in the test's tmp dir and never finds them.

    ⚑ Like :func:`_image_available`, this runs at COLLECTION time and must never
    raise. A ``podman info`` that is merely SLOW — a cold or contended daemon on a
    shared CI runner — used to escape as ``TimeoutExpired`` and abort collection of
    the entire suite, so the unit job reported ``1 error`` having run ZERO tests.
    The timeout is a failure to learn the host storage, which this function's own
    contract already describes as ``None``; it is not a reason to fail a run that
    was not going to touch podman at all.
    """
    if _podman is None:
        return None
    try:
        result = subprocess.run(
            [_podman, "info", "--format",
             "{{.Store.GraphRoot}}\n{{.Store.RunRoot}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
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


@contextmanager
def _active_env(env: dict[str, str]) -> Iterator[None]:
    """Temporarily install *env*'s HOME/XDG_* into ``os.environ``.

    The CLI runs as a subprocess with an isolated env *dict*; to resolve the
    box's host-side paths from the test process we temporarily install that same
    env into ``os.environ`` and drive the REAL path resolver (no hand-rolled
    path math, so the test stays faithful to what launch actually computes).
    """
    keys = (
        "HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_CACHE_HOME",
    )
    saved = {k: os.environ.get(k) for k in keys}
    try:
        for k in keys:
            if k in env:
                os.environ[k] = env[k]
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def resolve_box_dir(env: dict[str, str], project: Path) -> Path:
    """Resolve the host-side box METADATA dir for *project* via the real resolver.

    A ``kanibako create <path> --name <n>`` box is workset-scoped: its metadata
    dir lives at ``$XDG_DATA_HOME/kanibako/<workset>/boxes/<name>`` (e.g.
    ``.../kanibako/primary_workset/boxes/<name>``), NOT the legacy
    ``.../kanibako/boxes/<name>``.  We resolve it through the SAME functions the
    launch path uses (``ProjectPaths.metadata_path`` == the ``boxes/<name>/``
    dir that contains ``home/``, i.e. ``proj.shell_path.parent``), so the
    inspected path matches what ``start``/``rm`` actually wrote/removed.
    """
    from kanibako.config import config_file_path, load_config
    from kanibako.paths import load_std_paths, resolve_project

    with _active_env(env):
        config_file = config_file_path(Path(env["XDG_CONFIG_HOME"]))
        config = load_config(config_file)
        std = load_std_paths(config)
        proj = resolve_project(
            std, config, project_dir=str(project), initialize=False
        )
    return proj.metadata_path


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
def goose_stub_script() -> Path:
    """Return path to the goose stub script."""
    stub = Path(__file__).parent / "fixtures" / "goose-stub"
    assert stub.is_file(), f"Goose stub not found: {stub}"
    assert os.access(stub, os.X_OK), f"Goose stub not executable: {stub}"
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
    graphroot (host_storage_conf) so they share the host's image store, where
    the workflow tagged the pulled image. On the standard CI/local setup the
    pinned store *is* the default store, so the image is already visible and
    this fixture is a no-op beyond the diagnostics (confirmed on CI: the
    pre-warm check adds <1s and never pulls). It exists as defense-in-depth:
    if a custom graphroot ever makes the pinned store diverge from the store
    the image was tagged into, the first ``kanibako start`` would otherwise
    cold-pull *inside* a test and blow SUBPROCESS_TIMEOUT; pre-warming here --
    once, under the exact config the subprocesses see, on the session clock --
    removes that risk. The diagnostics it logs (captured vs pinned graphroot,
    image presence) make any such divergence obvious in the run summary.
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


@pytest.fixture(autouse=True)
def _reap_subuid_owned_tmp(tmp_path):
    """Delete each test's tmp dir through the SUBUID-AWARE deleter.

    ⚑ WHY pytest's own cleanup is not enough.  Since J-7 a created box home holds a
    ROOT-OWNED canon skeleton — chowned to a mapped subuid the host user cannot
    unlink — so pytest's plain ``rmtree`` of ``/tmp/pytest-of-<user>/...`` fails with
    ``PermissionError`` on any host with working rootless userns.  The dirs then
    accumulate across runs (the gate measured the creep on both real-podman legs) and
    eventually the numbered-dir retention sweep starts failing too.

    ⚑ E2E ONLY, and deliberately.  On a host WITHOUT working ``podman unshare`` (the
    dev box) nothing is ever protected, so there is nothing to reap and this is a
    no-op; and the unit suite never creates a protected box.  Wiring it globally
    would put a podman escalation in the path of every unit test for a problem only
    real-podman e2e hosts have.

    Best-effort by contract: ``remove_box_tree`` returns False rather than raising,
    and a failure here must never turn a passing test red.
    """
    yield
    try:
        from kanibako.container import remove_box_tree

        if tmp_path.exists():
            remove_box_tree(tmp_path)
    except Exception as exc:  # noqa: BLE001 - cleanup must never fail a test
        _diag(f"tmp reap skipped for {tmp_path}: {exc}")


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
    kanibako_config = config_home / "kanibako_config.yaml"
    kanibako_config.write_text(
        f'kanibako:\n  image: "{E2E_IMAGE}"\n'
    )
    # This fixture PRE-SEEDS the config instead of running first-run init, so it
    # must also record the template-staleness stamp the way init does
    # (cli.py: write_system_value("templates_stamp", packaged_templates_digest(...))).
    # Otherwise template_staleness_gate sees "config file exists + no stamp"
    # (None != digest) and HARD-ERRORS every `kanibako start` with "bundled
    # templates changed since setup was last run", so no container is ever created.
    # Computed over the SAME sorted(discover_targets()) set the gate recomputes in
    # the subprocess, so host + subprocess digests match.
    from kanibako.config_interface import write_system_value
    from kanibako.targets import discover_targets
    from kanibako.templates import packaged_templates_digest
    write_system_value(
        kanibako_config,
        "templates_stamp",
        packaged_templates_digest(sorted(discover_targets().keys())),
    )

    # Create a name file so container_name_for() gives a predictable name
    # We register via kanibako create later, but for name computation we
    # need the names.yaml to exist.
    names_dir = data_home / "kanibako"
    names_dir.mkdir(parents=True)

    # Pin the system default agent to "claude" so these claude-only tests resolve
    # the agent unambiguously even when OTHER agent plugins (e.g.
    # kanibako-agent-goose, required by the goose cred-sync e2e) are also
    # installed in the test environment.  Without this, config.resolve_agent
    # falls through to the installed-count rule, which raises NoAgentSelectedError
    # ("No agent selected") when 2+ real agents are installed, so `kanibako start`
    # exits rc=1 and no container is ever created.  The system settings tier
    # (@config.settings = $XDG_DATA_HOME/kanibako/global/settings.yaml) holds the
    # ``system.agent`` SETTING in its ``system:`` table, which the SYSTEM tier of
    # the settings cascade reads; it wins BEFORE the installed-count rule, so this
    # is faithful to real usage (a configured default agent) and fixes the
    # ambiguity in one place rather than threading --agent through every call.
    # ⮕ P7: this file used to carry ``agent.default.default_agent`` — the RETIRED
    # spelling, which the launch now REFUSES BY NAME (migration M-4). Writing the
    # new location here is also the in-repo proof of the new store shape.
    system_settings = names_dir / "global" / "settings.yaml"
    system_settings.parent.mkdir(parents=True, exist_ok=True)
    system_settings.write_text("system:\n  agent: claude\n")

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


# Known host credential content the goose cred-sync tests seed and assert on.
# Exported so a seadog run can cross-check exactly what the box should observe.
GOOSE_SECRETS_TOKEN = "e2e-goose-secret-aardvark-1234"
GOOSE_SECRETS_CONTENT = (
    "ANTHROPIC_API_KEY: " + GOOSE_SECRETS_TOKEN + "\n"
)
GOOSE_CONFIG_CONTENT = (
    "GOOSE_PROVIDER: anthropic\n"
    "GOOSE_MODEL: claude-e2e-stub\n"
)


@pytest.fixture()
def goose_e2e_env(tmp_path, goose_stub_script, host_storage_conf) -> dict:
    """Create an isolated e2e environment for a GOOSE box.

    Mirrors :func:`e2e_env` but targets the goose plugin instead of claude:

      - isolated HOME / XDG dirs
      - the goose stub placed at ``~/.local/bin/goose`` so
        ``GooseTarget.detect()`` (which anchors to that exact contract path,
        NOT ``$PATH``) resolves it
      - host ``~/.config/goose/secrets.yaml`` + ``config.yaml`` seeded with
        KNOWN content (GOOSE_SECRETS_CONTENT / GOOSE_CONFIG_CONTENT) so the
        credsync engine has real SYNC cred files to deliver / write back
      - ``kanibako_config.yaml`` pinned to the e2e image
      - the same ``CONTAINERS_STORAGE_CONF`` pin as ``e2e_env`` so the
        kanibako-launched podman sees the host's image store

    Returns the same dict shape as ``e2e_env`` plus:
      - "goose_config_dir": Path to host ``~/.config/goose``
      - "secrets_path", "config_path": Paths to the seeded host cred files
    """
    home = tmp_path / "home"
    config_home = tmp_path / "config"
    data_home = tmp_path / "data"
    state_home = tmp_path / "state"
    cache_home = tmp_path / "cache"
    project = tmp_path / "project"

    for d in (home, config_home, data_home, state_home, cache_home, project):
        d.mkdir()

    # Goose detection anchors to ~/.local/bin/goose (the _BINARY contract path),
    # so the stub must live there exactly.  detect() resolves the real ELF and
    # binds it into the box as the entrypoint, so the stub is also what runs
    # in-box.
    goose_bin_dir = home / ".local" / "bin"
    goose_bin_dir.mkdir(parents=True)
    goose_binary = goose_bin_dir / "goose"
    shutil.copy2(goose_stub_script, goose_binary)
    goose_binary.chmod(0o755)

    # Seed host cred files with KNOWN content so refresh/seed_cred_files have a
    # real SYNC source and the seed assertion has a recognizable sentinel.
    goose_config_dir = home / ".config" / "goose"
    goose_config_dir.mkdir(parents=True)
    secrets_path = goose_config_dir / "secrets.yaml"
    config_path = goose_config_dir / "config.yaml"
    secrets_path.write_text(GOOSE_SECRETS_CONTENT)
    config_path.write_text(GOOSE_CONFIG_CONTENT)

    # Build kanibako config pinned to the e2e image.
    kanibako_config = config_home / "kanibako_config.yaml"
    kanibako_config.write_text(
        f'kanibako:\n  image: "{E2E_IMAGE}"\n'
    )
    # Record the template stamp the way first-run init does — this fixture
    # pre-seeds the config instead of running init, so without the stamp
    # template_staleness_gate hard-errors every `kanibako start` (see e2e_env).
    from kanibako.config_interface import write_system_value
    from kanibako.targets import discover_targets
    from kanibako.templates import packaged_templates_digest
    write_system_value(
        kanibako_config,
        "templates_stamp",
        packaged_templates_digest(sorted(discover_targets().keys())),
    )

    names_dir = data_home / "kanibako"
    names_dir.mkdir(parents=True)

    env = os.environ.copy()
    env.update({
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(config_home),
        "XDG_DATA_HOME": str(data_home),
        "XDG_STATE_HOME": str(state_home),
        "XDG_CACHE_HOME": str(cache_home),
        # Goose detect() does NOT consult $PATH (it anchors to ~/.local/bin),
        # but keep the stub dir on PATH for parity with e2e_env and so any
        # incidental `goose` lookup resolves to the stub.
        "PATH": f"{goose_bin_dir}:{env.get('PATH', '')}",
        # Pin rootless podman storage back to the host's real graphroot (see
        # e2e_env / host_storage_conf): HOME/XDG overrides above would otherwise
        # relocate it into tmp_path and hide the host's pulled images.
        "CONTAINERS_STORAGE_CONF": str(host_storage_conf),
    })

    yield {
        "env": env,
        "home": home,
        "project": project,
        "config_home": config_home,
        "data_home": data_home,
        "stub_script": goose_stub_script,
        "goose_config_dir": goose_config_dir,
        "secrets_path": secrets_path,
        "config_path": config_path,
        "tmp_path": tmp_path,
    }

    # Teardown: prefix-based cleanup of this test's containers (mirrors e2e_env).
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
        if name.startswith("kanibako-e2e-"):
            subprocess.run(
                [_podman, "rm", "-f", "-t", "1", name],
                capture_output=True,
                timeout=10,
            )

"""E2E regression tests for the seed-at-create model through the REAL paths.

These are the tests that guard against the ``1.6.0.dev30`` data-loss
regression: a build that was unit-green yet, on a live box launch, RE-SEEDED a
box's already-populated home and CLOBBERED the user's ``~/playbook`` (and the
other template files) by overwriting them on every relaunch.

Under the NEW seed model (B7), seeding is bound to ``kanibako create``, not to
``kanibako start``:

* ``kanibako create`` SEEDS the box home ATOMICALLY at creation (before/with
  registration).  Registry MEMBERSHIP is therefore the seed signal — there is
  no per-box ``seeded`` flag and no lazy first-launch seeding.
* ``kanibako start`` NEVER seeds.  A relaunch can never re-seed, so any user
  edits to the seeded files survive every launch.  The seed step itself is
  create-if-absent, so the failsafe is now "seed-at-create + launch-never-seeds"
  rather than the old "seeded-flag ORed with an inbox backstop".

Unit tests cannot fully catch the clobber class of bug because it only
manifests through the FULL pipeline: ``create`` (the ``seeded`` category,
create-if-absent) → registration → the real reconcile model → ``start``
(which must touch none of the seeded content).  Every test here drives the
real CLI as a subprocess against real podman and inspects the box's host-side
home afterwards, so none of that path is stubbed.

Run on the real-runtime (LXC/VM) marked set, e.g.::

    KANI_PYTEST_MARK=e2e KANI_PYTEST_INCLUDE_E2E=1 \\
        ~/canon/notebook/scripts/chunked-pytest.sh tests/e2e/test_seed_relaunch.py

or directly::

    pytest -m e2e tests/e2e/test_seed_relaunch.py -v

A real-runtime run is REQUIRED to validate behavior: this file is authored
against the e2e harness but cannot execute where podman is absent.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

from tests.e2e.conftest import (
    e2e_requires,
    run_kanibako,
    wait_for_container,
)

pytestmark = [pytest.mark.e2e, *e2e_requires]

# The seeded category dest.  ⚑ REPOINTED off the retired ``~/playbook`` to the box's
# own NOTEBOOK — the canon's writable, box-owned book — because that is where a box's
# durable notes live now, and because it exercises the one part of ``~/canon`` that
# must stay AGENT-WRITABLE while everything around it is root-owned 555.
#
# It stays a USER-DECLARED ``system.seeded.<name>`` key, deliberately: a user-declared
# seed is GUEST-space (``~/...``, translated by ``_guest_dest_to_host``), so this test
# also keeps covering the guest arm of the two-namespace split while the packaged
# layers cover the host arm.  ``~/canon/notebook`` expands, in guest space, to
# ``/home/agent/canon/notebook``; on the host it lands under
# ``<shell_path>/canon/notebook``.
SEED_GUEST_DEST = "~/canon/notebook"
SEED_FILENAME = "devnotes.md"
PRISTINE_SEED_CONTENT = "# devnotes (pristine seed)\nseeded at create\n"
EDITED_CONTENT = "# devnotes (EDITED IN BOX)\nirreplaceable session notes — DO NOT CLOBBER\n"


# ---------------------------------------------------------------------------
# In-process inspection helpers
#
# The CLI runs as a subprocess with an isolated env *dict*; to inspect the
# box's host-side home from the test process we temporarily install that same
# env into ``os.environ`` and drive the REAL path/seed/channel code (no
# hand-rolled path math, so the test stays faithful to what create/launch
# actually computes).  Under the new model the "is this box seeded?" signal is
# CONTENT-BASED: the seeded ``~/canon/notebook/<SEED_FILENAME>`` file is present iff
# the box home was seeded (which happens at create, never at launch).
# ---------------------------------------------------------------------------


@contextmanager
def _active_env(env: dict[str, str]) -> Iterator[None]:
    """Temporarily install *env*'s HOME/XDG_* into ``os.environ``."""
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


def _resolve_proj(env: dict[str, str], project: Path):
    """Build the REAL (std, proj) pair for *project* under the subprocess env.

    ``kanibako create <path> --name <n>`` produces a PRIMARY-mode box whose
    home is ``proj.shell_path``.  We resolve via the same functions the
    create/launch paths use so the inspected paths match what the CLI wrote.
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
    return std, proj


def _shell_path(env: dict[str, str], project: Path) -> Path:
    """Host-side box home (maps to /home/agent in the container)."""
    _std, proj = _resolve_proj(env, project)
    return proj.shell_path


def _seed_file(env: dict[str, str], project: Path) -> Path:
    """Host path of the seeded ``~/canon/notebook/<SEED_FILENAME>`` file."""
    return _shell_path(env, project) / "canon" / "notebook" / SEED_FILENAME


def _home_is_seeded(env: dict[str, str], project: Path) -> bool:
    """Content-based seed signal: is the seeded home file present on disk?

    There is no per-box ``seeded`` flag under the new model — the box home is
    seeded iff its ``~/canon/notebook/<SEED_FILENAME>`` file exists (written at
    create, never at launch).
    """
    return _seed_file(env, project).exists()


def _write_seed_config(env: dict[str, str], host_seed_dir: Path) -> None:
    """Configure ``system.seeded.notebook`` in the system settings file.

    The create path reads ``seeded`` category keys from ``@system.settings`` ==
    ``{XDG_DATA_HOME}/kanibako/global/settings.yaml`` (see
    ``_category_resolution_inputs``).  We point a ``~/canon/notebook``-style seed at
    *host_seed_dir*, mirroring the user's real per-agent seed entry that was
    clobbered.  The value form is a structured ``[host_src, guest_dest]``
    pair (the keyspace rework rejects the legacy ``<host_src>:<guest_dest>`` string).

    This MERGES into any existing settings document rather than overwriting it:
    the ``e2e_env`` fixture writes ``system.agent: claude`` into
    this SAME file (so the claude-only tests resolve an agent even when other
    plugins are installed), and a blind overwrite here would wipe that key and
    re-introduce the dual-agent "No agent selected" ambiguity that prevents the
    box from launching.  We load the existing doc, add ``system.seeded.notebook``,
    and write it back, preserving the ``system`` content.
    """
    from kanibako.config import config_file_path, load_config
    from kanibako.config_io import dump_doc, load_doc
    from kanibako.paths import load_std_paths

    with _active_env(env):
        config_file = config_file_path(Path(env["XDG_CONFIG_HOME"]))
        config = load_config(config_file)
        std = load_std_paths(config)
        settings_file = std.settings

    settings_file.parent.mkdir(parents=True, exist_ok=True)
    # Merge into the existing settings doc (preserving e2e_env's
    # system.agent) rather than clobbering it.  The value is a
    # structured [host_src, box_dest] pair (the keyspace rework rejects the
    # legacy "host:dest" colon-string form).
    doc = load_doc(settings_file)
    doc.setdefault("system", {})["seeded"] = {
        "notebook": [str(host_seed_dir), SEED_GUEST_DEST]
    }
    dump_doc(settings_file, doc)


def _make_host_seed(tmp_path: Path) -> Path:
    """Create the host-side seed source dir with one pristine file."""
    src = tmp_path / "seed_src" / "notebook"
    src.mkdir(parents=True)
    (src / SEED_FILENAME).write_text(PRISTINE_SEED_CONTENT)
    return src


def _stop(env: dict[str, str], name: str) -> None:
    """Best-effort stop so the next start is a clean relaunch."""
    run_kanibako(["stop", name], env=env)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSeedAtCreate:
    """The B7 seed model: seed-at-create, membership-is-the-signal, and a
    relaunch must NEVER (re-)seed or clobber box home content."""

    def test_create_seeds_home(self, e2e_env):
        """``create`` (NO start yet) seeds the home with the pristine content.

        Headline new behavior: seeding is bound to creation, so the seeded
        file exists immediately after ``kanibako create``, before any launch.
        """
        env = e2e_env["env"]
        project = e2e_env["project"]
        name = "e2e-seed-create"

        host_seed = _make_host_seed(e2e_env["tmp_path"])
        _write_seed_config(env, host_seed)

        # Before create: nothing on disk.
        assert not _home_is_seeded(env, project)

        result = run_kanibako(
            ["create", str(project), "--name", name], env=env
        )
        assert result.returncode == 0, f"create failed: {result.stderr}"

        # Seed happened AT CREATE — before any launch.
        seed_file = _seed_file(env, project)
        assert _home_is_seeded(env, project), (
            "create must seed ~/canon/notebook atomically (no launch required)"
        )
        assert seed_file.read_text() == PRISTINE_SEED_CONTENT

    def test_edited_seed_survives_relaunch(self, e2e_env):
        """create (seeds) → edit-in-box → launch → relaunch → edits SURVIVE.

        This is the exact data-loss scenario dev30 shipped, now guarded by the
        seed model: launch never seeds, so an edited seed file is never
        clobbered across any number of relaunches.
        """
        env = e2e_env["env"]
        project = e2e_env["project"]
        name = "e2e-seed-clobber"
        container = f"kanibako-{name}"

        host_seed = _make_host_seed(e2e_env["tmp_path"])
        _write_seed_config(env, host_seed)

        result = run_kanibako(
            ["create", str(project), "--name", name], env=env
        )
        assert result.returncode == 0, f"create failed: {result.stderr}"

        # Create seeded the home with pristine content.
        seed_file = _seed_file(env, project)
        assert _home_is_seeded(env, project), "create must seed ~/canon/notebook"
        assert seed_file.read_text() == PRISTINE_SEED_CONTENT

        # Edit the seeded file in the box home (distinctive content).
        seed_file.write_text(EDITED_CONTENT)

        # FIRST launch — must NOT seed (membership is the signal) → no clobber.
        run_kanibako(
            ["start", name, "-e", "CLAUDE_STUB_MODE=long-running"], env=env
        )
        wait_for_container(container, timeout=15)
        assert seed_file.exists(), "edited seed file vanished after launch"
        assert seed_file.read_text() == EDITED_CONTENT, (
            "LAUNCH re-seeded and clobbered the edited ~/canon/notebook file"
        )
        _stop(env, name)

        # RELAUNCH — must still NOT seed and NOT clobber.
        run_kanibako(
            ["start", name, "-e", "CLAUDE_STUB_MODE=long-running"], env=env
        )
        wait_for_container(container, timeout=15)
        assert seed_file.exists(), "edited seed file vanished after relaunch"
        assert seed_file.read_text() == EDITED_CONTENT, (
            "RELAUNCH CLOBBERED the edited ~/canon/notebook file (the dev30 "
            "data-loss regression)"
        )
        _stop(env, name)

    def test_launch_never_seeds_after_delete(self, e2e_env):
        """create (seeds) → delete the seeded file → launch → NOT recreated.

        Proves seeding is bound to create, not launch: once the create-time
        seed is removed, no launch re-runs the seed pass.  (The create-if-absent
        failsafe lives in ``create``, never in ``start``.)
        """
        env = e2e_env["env"]
        project = e2e_env["project"]
        name = "e2e-seed-delete"
        container = f"kanibako-{name}"

        host_seed = _make_host_seed(e2e_env["tmp_path"])
        _write_seed_config(env, host_seed)

        result = run_kanibako(
            ["create", str(project), "--name", name], env=env
        )
        assert result.returncode == 0, f"create failed: {result.stderr}"

        seed_file = _seed_file(env, project)
        assert _home_is_seeded(env, project), "create must seed ~/canon/notebook"

        # Remove the create-time seed, then launch.
        seed_file.unlink()
        assert not _home_is_seeded(env, project)

        run_kanibako(
            ["start", name, "-e", "CLAUDE_STUB_MODE=long-running"], env=env
        )
        wait_for_container(container, timeout=15)

        assert not _home_is_seeded(env, project), (
            "LAUNCH re-seeded the home — seeding must be bound to create, "
            "not to start"
        )
        _stop(env, name)

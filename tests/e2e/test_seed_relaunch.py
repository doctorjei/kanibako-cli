"""E2E regression tests for the seed-once gate through the REAL launch path.

These are the tests that would have caught the ``1.6.0.dev30`` data-loss
regression: a build that was unit-green yet, on a live box launch, RE-SEEDED a
box's already-populated home and CLOBBERED the user's ``~/playbook`` (and the
other template files) by overwriting them on every relaunch.

Unit tests cannot catch that class of bug because the clobber only manifests
through the FULL ``kanibako start`` pipeline: detection (``_box_already_seeded``
ORing the per-box ``seeded`` registry flag with the inbox backstop) →
``_apply_init_seeds`` (the ``seeded`` category, create-if-absent) → the real
reconcile model → real inbox/mailbox creation timing.  Every test here drives
the real CLI as a subprocess against real podman and inspects the box's
host-side home + registry afterwards, so none of that path is stubbed.

Run on the real-runtime (LXC/VM) marked set, e.g.::

    KANI_PYTEST_MARK=e2e KANI_PYTEST_INCLUDE_E2E=1 \\
        ~/playbook/scripts/chunked-pytest.sh tests/e2e/test_seed_relaunch.py

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

# The seeded category dest, mirroring the user's real ``agent.seeded.playbook``
# (the file that was clobbered).  ``~/playbook`` expands, in guest space, to
# ``/home/agent/playbook``; on the host it lands under ``<shell_path>/playbook``.
SEED_GUEST_DEST = "~/playbook"
SEED_FILENAME = "devnotes.md"
PRISTINE_SEED_CONTENT = "# devnotes (pristine seed)\nseeded on first launch\n"
EDITED_CONTENT = "# devnotes (EDITED IN BOX)\nirreplaceable session notes — DO NOT CLOBBER\n"


# ---------------------------------------------------------------------------
# In-process inspection helpers
#
# The CLI runs as a subprocess with an isolated env *dict*; to inspect the
# box's host-side home + the per-box ``seeded`` registry flag from the test
# process we temporarily install that same env into ``os.environ`` and drive
# the REAL path/seed/channel code (no hand-rolled path math, so the test stays
# faithful to what launch actually computes).
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

    ``kanibako create <path> --name <n>`` produces a PRIMARY-mode box; its
    seeded flag lives in the registry ``projects`` domain and its home is
    ``proj.shell_path``.  We resolve via the same functions the launch path
    uses so the inspected paths match what ``start`` actually wrote.
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
    """Host path of the seeded ``~/playbook/<SEED_FILENAME>`` file."""
    return _shell_path(env, project) / "playbook" / SEED_FILENAME


def _inbox_path(env: dict[str, str], project: Path) -> Path:
    """Host path of the box's own mailbox (inbox) dir — the seed backstop."""
    from kanibako import channels

    std, proj = _resolve_proj(env, project)
    with _active_env(env):
        return channels.box_channel_addresses(proj, std).inbox


def _is_seeded_flag(env: dict[str, str], project: Path) -> bool:
    """Read the authoritative per-box ``seeded`` registry flag (no launch)."""
    from kanibako import box_seed

    std, proj = _resolve_proj(env, project)
    with _active_env(env):
        return box_seed.box_is_seeded(proj, std)


def _write_seed_config(env: dict[str, str], host_seed_dir: Path) -> None:
    """Configure ``system.seeded.playbook`` in the system settings file.

    The launch path reads ``seeded`` category keys from ``@system.settings`` ==
    ``{XDG_DATA_HOME}/kanibako/global/settings.yaml`` (see
    ``_category_resolution_inputs``).  We point a ``~/playbook``-style seed at
    *host_seed_dir*, mirroring the user's real ``agent.seeded.playbook`` entry
    that was clobbered.  The value form is a structured ``[host_src, guest_dest]``
    pair (the keyspace rework rejects the legacy ``<host_src>:<guest_dest>`` string).
    """
    from kanibako.config import config_file_path, load_config
    from kanibako.paths import load_std_paths

    with _active_env(env):
        config_file = config_file_path(Path(env["XDG_CONFIG_HOME"]))
        config = load_config(config_file)
        std = load_std_paths(config)
        settings_file = std.settings

    settings_file.parent.mkdir(parents=True, exist_ok=True)
    # Structured [host_src, box_dest] pair (the keyspace rework rejects the
    # legacy "host:dest" colon-string form).
    settings_file.write_text(
        "system:\n"
        "  seeded:\n"
        f'    playbook: ["{host_seed_dir}", "{SEED_GUEST_DEST}"]\n'
    )


def _make_host_seed(tmp_path: Path) -> Path:
    """Create the host-side seed source dir with one pristine file."""
    src = tmp_path / "seed_src" / "playbook"
    src.mkdir(parents=True)
    (src / SEED_FILENAME).write_text(PRISTINE_SEED_CONTENT)
    return src


def _stop(env: dict[str, str], name: str) -> None:
    """Best-effort stop so the next start is a clean relaunch."""
    run_kanibako(["stop", name], env=env)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSeedRelaunchClobber:
    """The dev30 regression: a relaunch must NOT clobber an edited seed."""

    def test_edited_seed_survives_relaunch(self, e2e_env):
        """seed → edit-in-box → relaunch → edited content SURVIVES (no clobber).

        This is the exact data-loss scenario dev30 shipped: the seed-once gate
        re-seeds on a later launch and overwrites the box's own home content.
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

        # Fresh box: not yet seeded, no home seed file on disk.
        assert _is_seeded_flag(env, project) is False
        assert not _seed_file(env, project).exists()

        # FIRST launch — seeds the home via the real _apply_init_seeds path.
        run_kanibako(
            ["start", name, "-e", "CLAUDE_STUB_MODE=long-running"], env=env
        )
        wait_for_container(container, timeout=15)

        seed_file = _seed_file(env, project)
        assert seed_file.exists(), "first launch must seed ~/playbook"
        assert seed_file.read_text() == PRISTINE_SEED_CONTENT
        assert _is_seeded_flag(env, project) is True, (
            "first launch must stamp the per-box seeded flag"
        )

        # Edit the seeded file in the box home (distinctive content).
        seed_file.write_text(EDITED_CONTENT)
        _stop(env, name)

        # RELAUNCH — must skip seeding (already-seeded) and NOT clobber.
        run_kanibako(
            ["start", name, "-e", "CLAUDE_STUB_MODE=long-running"], env=env
        )
        wait_for_container(container, timeout=15)

        assert seed_file.exists(), "edited seed file vanished after relaunch"
        assert seed_file.read_text() == EDITED_CONTENT, (
            "RELAUNCH CLOBBERED the edited ~/playbook file (the dev30 "
            "data-loss regression)"
        )
        _stop(env, name)

    def test_fresh_box_gets_seed_on_first_launch(self, e2e_env):
        """A brand-new box receives the seeded content on its first launch."""
        env = e2e_env["env"]
        project = e2e_env["project"]
        name = "e2e-seed-fresh"
        container = f"kanibako-{name}"

        host_seed = _make_host_seed(e2e_env["tmp_path"])
        _write_seed_config(env, host_seed)

        result = run_kanibako(
            ["create", str(project), "--name", name], env=env
        )
        assert result.returncode == 0, f"create failed: {result.stderr}"

        # Nothing seeded before the first launch.
        assert not _seed_file(env, project).exists()
        assert _is_seeded_flag(env, project) is False

        run_kanibako(
            ["start", name, "-e", "CLAUDE_STUB_MODE=long-running"], env=env
        )
        wait_for_container(container, timeout=15)

        seed_file = _seed_file(env, project)
        assert seed_file.exists(), "fresh box did not receive its seed"
        assert seed_file.read_text() == PRISTINE_SEED_CONTENT
        assert _is_seeded_flag(env, project) is True
        _stop(env, name)

    def test_legacy_box_is_adopted_not_reseeded(self, e2e_env):
        """A pre-redesign box (existing home/inbox, no seeded flag) is ADOPTED.

        Simulates a box created before the per-box ``seeded`` registry flag
        existed: its home + inbox already exist but the flag is False (the old
        ``.seeded`` sentinel is gone).  The launch path must detect it as
        already-seeded via the inbox backstop, NOT re-seed (so existing home
        content is never clobbered), and ADOPT it by stamping the flag.
        """
        env = e2e_env["env"]
        project = e2e_env["project"]
        name = "e2e-seed-legacy"
        container = f"kanibako-{name}"

        host_seed = _make_host_seed(e2e_env["tmp_path"])
        _write_seed_config(env, host_seed)

        result = run_kanibako(
            ["create", str(project), "--name", name], env=env
        )
        assert result.returncode == 0, f"create failed: {result.stderr}"

        # Forge a legacy on-disk state: a populated home with pre-existing
        # playbook content, plus the inbox dir that the backstop keys on — but
        # the registry seeded flag stays False (no .seeded sentinel survives).
        seed_file = _seed_file(env, project)
        seed_file.parent.mkdir(parents=True, exist_ok=True)
        seed_file.write_text(EDITED_CONTENT)
        _inbox_path(env, project).mkdir(parents=True, exist_ok=True)

        assert _is_seeded_flag(env, project) is False, (
            "precondition: legacy box has no seeded flag"
        )

        # Launch: detection must OR in the inbox backstop → already-seeded →
        # skip seeding.  No clobber, and the flag is adopt-stamped.
        run_kanibako(
            ["start", name, "-e", "CLAUDE_STUB_MODE=long-running"], env=env
        )
        wait_for_container(container, timeout=15)

        assert seed_file.read_text() == EDITED_CONTENT, (
            "legacy adoption RE-SEEDED and clobbered existing home content"
        )
        assert _is_seeded_flag(env, project) is True, (
            "legacy box was not adopted (seeded flag never stamped)"
        )
        _stop(env, name)

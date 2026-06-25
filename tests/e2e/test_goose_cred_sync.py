"""E2E tests for REAL goose credential sync (host <-> box).

These guard the step-4 goose-credsync consolidation: the goose plugin's bespoke
``refresh_secrets`` / ``writeback_secrets`` overrides were DELETED, and goose now
declares ``secrets.yaml`` / ``config.yaml`` / ``custom_providers/`` as SYNC
``CredFileSpec`` entries in ``goose-defaults.yaml``, realized by the shared
credsync engine (``seed_cred_files`` / ``refresh_cred_files`` /
``writeback_cred_files``).  If that consolidation ever regresses (e.g. a spec
dropped, the mtime gate inverted, the engine not wired into start/stop), these
tests fail end-to-end with a real container, where unit tests cannot.

They require podman + tmux + the e2e image, so they SKIP on a dev box without a
runtime (that is expected; the real run is on seadog).

Box-home model used below: ``proj.shell_path`` (the host project shell dir) is
bind-mounted as ``/home/agent`` in the box, so the in-box file
``/home/agent/.config/goose/secrets.yaml`` IS the host file
``proj.shell_path/.config/goose/secrets.yaml``.  refresh/seed copies the user's
real host ``~/.config/goose/secrets.yaml`` into that project copy (delivery),
and writeback copies the project copy back to the real host home (persistence).
"""

from __future__ import annotations

import time

import pytest

from tests.e2e.conftest import (
    GOOSE_SECRETS_TOKEN,
    e2e_requires,
    podman_exec,
    run_kanibako,
    wait_for_container,
)

pytestmark = [pytest.mark.e2e, *e2e_requires]

# In-box path to goose's secrets file (config_dir_name = ".config/goose").
BOX_SECRETS = "/home/agent/.config/goose/secrets.yaml"


def _goose_plugin_available() -> bool:
    """Check if the kanibako-agent-goose plugin is importable."""
    try:
        import kanibako.plugins.goose  # noqa: F401
        return True
    except ImportError:
        return False


requires_goose_plugin = pytest.mark.skipif(
    not _goose_plugin_available(),
    reason="kanibako-agent-goose plugin not installed",
)


@requires_goose_plugin
class TestGooseCredSync:
    """Real host<->box sync of goose's SYNC cred files via the credsync engine."""

    def test_goose_secrets_synced_into_box(self, goose_e2e_env):
        """Host ``secrets.yaml`` is delivered into the goose box.

        Proves ``seed_cred_files`` / ``refresh_cred_files`` realize goose's
        secrets.yaml SYNC CredFileSpec: the KNOWN host token appears inside the
        box at ``~/.config/goose/secrets.yaml``.  Guards step-4's removal of
        goose's bespoke ``refresh_secrets``.
        """
        env = goose_e2e_env["env"]
        project = goose_e2e_env["project"]

        result = run_kanibako(
            ["create", str(project), "--name", "e2e-goose-seed"],
            env=env,
        )
        assert result.returncode == 0, (
            f"create failed: {result.stdout}\n{result.stderr}"
        )

        # --agent goose forces GooseTarget selection; long-running keeps the box
        # up so we can exec into it.
        run_kanibako(
            ["start", "e2e-goose-seed", "--agent", "goose",
             "-e", "GOOSE_STUB_MODE=long-running"],
            env=env,
        )
        wait_for_container("kanibako-e2e-goose-seed", timeout=15)

        # The secrets file must exist in the box...
        exists = podman_exec(
            "kanibako-e2e-goose-seed",
            ["test", "-f", BOX_SECRETS],
        )
        assert exists.returncode == 0, (
            "goose secrets.yaml not delivered into the box"
        )

        # ...and carry the KNOWN host token (the seed/refresh sync sentinel).
        cat_result = podman_exec(
            "kanibako-e2e-goose-seed",
            ["cat", BOX_SECRETS],
        )
        assert GOOSE_SECRETS_TOKEN in cat_result.stdout, (
            f"expected host token {GOOSE_SECRETS_TOKEN!r} in box secrets.yaml, "
            f"got:\n{cat_result.stdout}"
        )

    def test_goose_secrets_writeback_to_host(self, goose_e2e_env):
        """In-box ``secrets.yaml`` edits are written back to the host on stop.

        Proves ``writeback_cred_files`` realizes goose's secrets.yaml SYNC spec
        on the stop path (sourced from the box's ``KANIBAKO_AGENT`` launch
        stamp).  Guards step-4's removal of goose's bespoke ``writeback_secrets``.

        Accounts for the ``mtime_gate``: writeback only copies when the project
        copy is strictly NEWER than the host file, so we rewrite the in-box file
        (same bind-mounted inode as the project copy) AND bump its mtime past the
        host's seeded file before stopping.
        """
        env = goose_e2e_env["env"]
        project = goose_e2e_env["project"]
        host_secrets = goose_e2e_env["secrets_path"]

        result = run_kanibako(
            ["create", str(project), "--name", "e2e-goose-writeback"],
            env=env,
        )
        assert result.returncode == 0, (
            f"create failed: {result.stdout}\n{result.stderr}"
        )

        run_kanibako(
            ["start", "e2e-goose-writeback", "--agent", "goose",
             "-e", "GOOSE_STUB_MODE=long-running"],
            env=env,
        )
        wait_for_container("kanibako-e2e-goose-writeback", timeout=15)

        # Modify the in-box secrets file with a NEW recognizable token, then bump
        # its mtime well past the host file so the mtime_gate lets writeback copy.
        new_token = "e2e-goose-writeback-bobcat-5678"
        write = podman_exec(
            "kanibako-e2e-goose-writeback",
            ["sh", "-c",
             f"printf 'ANTHROPIC_API_KEY: {new_token}\\n' > {BOX_SECRETS} && "
             f"touch -d '2099-01-01' {BOX_SECRETS}"],
        )
        assert write.returncode == 0, (
            f"failed to rewrite in-box secrets: {write.stdout}\n{write.stderr}"
        )

        # Stop the box: stop.py sources KANIBAKO_AGENT=goose from the container
        # and funnels through writeback_session_credentials ->
        # writeback_cred_files (project -> host).
        stop = run_kanibako(["stop", "e2e-goose-writeback"], env=env)
        assert stop.returncode == 0, (
            f"stop failed: {stop.stdout}\n{stop.stderr}"
        )

        # Host secrets.yaml must now reflect the in-box change.  Allow a brief
        # settle for the writeback filesystem copy.
        deadline = time.monotonic() + 5.0
        content = ""
        while time.monotonic() < deadline:
            content = host_secrets.read_text()
            if new_token in content:
                break
            time.sleep(0.2)
        assert new_token in content, (
            f"expected writeback token {new_token!r} in host secrets.yaml, "
            f"got:\n{content}"
        )

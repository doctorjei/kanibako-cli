"""Integration tests for kanibako CLI lifecycle operations.

Tests exercise the kanibako CLI via subprocess calls in isolated environments.
Run with::

    pytest -m integration tests/test_lifecycle_integration.py -v
"""

from __future__ import annotations

import subprocess
import time

import pytest
import yaml

from tests.conftest_integration import requires_runtime

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TIMEOUT = 120  # seconds — generous for CI


def _run_kanibako(
    *args: str,
    env: dict[str, str],
    cwd: str | None = None,
    timeout: int = _TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    """Run the kanibako CLI as a subprocess with the given environment."""
    return subprocess.run(
        ["kanibako", *args],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _setup_with_image(cli_env: dict, image: str) -> None:
    """Trigger lazy init via system info and override the configured container image."""
    result = _run_kanibako("system", "info", env=cli_env["env"], cwd=str(cli_env["project"]))
    assert result.returncode == 0, f"lazy init failed: {result.stderr}"

    # Patch the config to use the requested image.
    config_file = cli_env["config_home"] / "kanibako_config.yaml"
    text = config_file.read_text()
    import re

    text = re.sub(r'^(\s*)image\s*:\s*.*$', rf'\1image: "{image}"', text, flags=re.MULTILINE)
    config_file.write_text(text)

    # v1.7.0 explicit-create: launches (start / shell / bare / code) no longer
    # auto-create a box — they ERROR on an absent box. Create the box now (after
    # the image patch, so `create` persists the requested image into the box
    # meta) so the subsequent launch finds an existing box. Default mode, cwd
    # project — matching how the launch calls below are invoked.
    result = _run_kanibako("create", env=cli_env["env"], cwd=str(cli_env["project"]))
    assert result.returncode == 0, f"create failed: {result.stderr}"


# =========================================================================
# Setup
# =========================================================================


@pytest.mark.integration
class TestKanibakoLazyInit:
    """Verify lazy init creates expected files and dirs on first command."""

    def test_lazy_init_creates_config_and_dirs(self, cli_env):
        """Lazy init writes the config file, the agent stores, and the env SEED.

        ⚑ The env seed is a settings KEY now, not a file.  Lazy init used to
        write a docker-style ``<data>/env``; B9 (``77c4cf4``) retired that file
        under Jei's RQ-1 re-ruling and re-homed the seed to a declared
        ``system.env.COLORTERM`` key in the system settings file, which
        ``6e3d016`` then moved to ``box.env.COLORTERM`` — the scope that actually
        describes the value (still written into the SAME system settings file, as
        a downward table).  Both halves are asserted below — the file must be
        ABSENT, the key must be present.
        """
        result = _run_kanibako("system", "info", env=cli_env["env"], cwd=str(cli_env["project"]))
        assert result.returncode == 0, f"lazy init failed: {result.stderr}"

        config_file = cli_env["config_home"] / "kanibako_config.yaml"
        assert config_file.is_file(), "kanibako_config.yaml not created"

        data_path = cli_env["data_home"] / "kanibako"
        agents_dir = data_path / "agents"
        assert agents_dir.is_dir(), "agents dir not created"

        # The RETIRED half.  Nothing writes this path and nothing reads it; if
        # it reappears, the dead writer came back.  This assertion is the pin.
        assert not (data_path / "env").exists(), (
            "the retired docker-style env FILE was re-created by lazy init"
        )

        # The REPLACEMENT half.  ``cli._ensure_initialized`` seeds
        # ``COLORTERM=truecolor`` at BOX scope, written downward into the system
        # settings file (``@config.settings`` = ``@config.data/global/settings.yaml``)
        # via ``config_io.write_nested_key``.
        #
        # ⚑ MBR-2 — the QUEUED task that deletes that write entirely and declares
        # COLORTERM as a real DEFAULT instead.  Whoever lands MBR-2 lands here:
        # drop the COLORTERM assertion below (keep the env-FILE one above), since
        # a defaulted key is resolved, not stored.
        settings_file = data_path / "global" / "settings.yaml"
        assert settings_file.is_file(), "system settings file not created"
        stored = yaml.safe_load(settings_file.read_text()) or {}
        assert stored.get("box", {}).get("env", {}).get("COLORTERM") == "truecolor", (
            f"first-run box.env.COLORTERM seed missing; settings file holds: {stored!r}"
        )

    def test_lazy_init_idempotent(self, cli_env):
        """Running commands twice succeeds without errors (lazy init is idempotent)."""
        r1 = _run_kanibako("system", "info", env=cli_env["env"], cwd=str(cli_env["project"]))
        assert r1.returncode == 0

        r2 = _run_kanibako("system", "info", env=cli_env["env"], cwd=str(cli_env["project"]))
        assert r2.returncode == 0


# =========================================================================
# Image operations
# =========================================================================


@pytest.mark.integration
class TestKanibakoImageOps:
    """Verify kanibako rig commands."""

    def test_rig_list_runs(self, cli_env):
        """kanibako rig list exits 0 (lazy init triggers automatically)."""
        result = _run_kanibako(
            "rig", "list", env=cli_env["env"], cwd=str(cli_env["project"])
        )
        assert result.returncode == 0

    @requires_runtime
    def test_rig_prep_base(self, cli_env):
        """kanibako rig prep oci builds/pulls from bundled Containerfile."""
        result = _run_kanibako(
            "rig", "prep", "oci",
            env=cli_env["env"],
            cwd=str(cli_env["project"]),
            timeout=600,
        )
        assert result.returncode == 0, f"rig prep failed: {result.stderr}"


# =========================================================================
# Shell (container execution)
# =========================================================================


@pytest.mark.integration
class TestKanibakoShell:
    """Verify kanibako can run commands inside containers.

    Uses ``busybox:latest`` as a lightweight image.  The ``shell --entrypoint``
    flag overrides the entrypoint so the container runs a single command
    and exits.  ``shell`` is used (not ``start``) because it does not require
    a detectable agent.
    """

    @requires_runtime
    def test_shell_runs_command(self, cli_env, container_runtime_cmd):
        """kanibako shell --entrypoint runs a command and captures output."""
        _setup_with_image(cli_env, "busybox:latest")
        subprocess.run(
            [container_runtime_cmd, "pull", "busybox:latest"],
            capture_output=True, check=True,
        )

        result = _run_kanibako(
            "shell", "--ephemeral", "--entrypoint", "/bin/sh",
            "--", "-c", "echo hello-from-container",
            env=cli_env["env"],
            cwd=str(cli_env["project"]),
        )
        assert result.returncode == 0, f"shell failed: {result.stderr}"
        assert "hello-from-container" in result.stdout

    @requires_runtime
    def test_shell_workspace_mounted(self, cli_env, container_runtime_cmd):
        """The project directory is visible inside the container at /home/agent/workspace."""
        _setup_with_image(cli_env, "busybox:latest")
        subprocess.run(
            [container_runtime_cmd, "pull", "busybox:latest"],
            capture_output=True, check=True,
        )

        # Create a marker file in the project dir.
        marker = cli_env["project"] / "marker.txt"
        marker.write_text("workspace-ok\n")

        result = _run_kanibako(
            "shell", "--ephemeral", "--entrypoint", "/bin/cat",
            "--", "/home/agent/workspace/marker.txt",
            env=cli_env["env"],
            cwd=str(cli_env["project"]),
        )
        assert result.returncode == 0, f"shell failed: {result.stderr}"
        assert "workspace-ok" in result.stdout

    @requires_runtime
    def test_shell_env_comes_from_the_key_not_the_retired_file(
        self, cli_env, container_runtime_cmd,
    ):
        """``<scope>.env.<VAR>`` reaches the box; the retired env FILE does not.

        The full three-part RQ-1 contract (Jei's re-ruling, 2026-08-02), proved
        end to end on a real container.  It landed across two commits: (a) and (b)
        with B9 ``77c4cf4``, which retired the file and re-homed the seed; (c) with
        ``ade2570``, which added the notice naming the retired files.

        (a) **the replacement works** — a var set through
            ``kanibako system set system.env.<VAR>`` is visible in the box.
            SYSTEM scope is the faithful successor to what was a *global* env
            file;
        (b) **the retired path is genuinely dead** — a var written into the
            legacy ``<data>/env`` file does NOT arrive.  This is the half that
            matters: it is the only end-to-end proof in the tree that the old
            launch input is gone, and a unit test cannot give it;
        (c) **the retirement is announced, not silent** —
            ``start._warn_legacy_env_files`` names the stale file on stderr and
            gives the cure for its own tier.

        The two variables are deliberately given DIFFERENT names so that a pass
        on (a) can never be mistaken for a pass on (b).
        """
        _setup_with_image(cli_env, "busybox:latest")
        subprocess.run(
            [container_runtime_cmd, "pull", "busybox:latest"],
            capture_output=True, check=True,
        )

        # (a) The REPLACEMENT: the declared key, set through the real CLI verb.
        set_result = _run_kanibako(
            "system", "set", "system.env.MY_TEST_VAR=lifecycle-check",
            env=cli_env["env"], cwd=str(cli_env["project"]),
        )
        assert set_result.returncode == 0, f"system set failed: {set_result.stderr}"

        # (b) The RETIRED input: the docker-style file the launch no longer reads.
        legacy_env_file = cli_env["data_home"] / "kanibako" / "env"
        legacy_env_file.write_text("LEGACY_TEST_VAR=should-not-arrive\n")

        result = _run_kanibako(
            "shell", "--ephemeral", "--entrypoint", "/bin/sh",
            "--", "-c", "echo key=[$MY_TEST_VAR] legacy=[$LEGACY_TEST_VAR]",
            env=cli_env["env"],
            cwd=str(cli_env["project"]),
        )
        assert result.returncode == 0, f"shell failed: {result.stderr}"

        # (a) delivered
        assert "key=[lifecycle-check]" in result.stdout, (
            f"system.env.MY_TEST_VAR did not reach the box: {result.stdout!r}"
        )
        # (b) NOT delivered — the point of the rewrite
        assert "legacy=[]" in result.stdout, (
            f"the retired env FILE still reaches the box: {result.stdout!r}"
        )
        assert "should-not-arrive" not in result.stdout

        # (c) announced on stderr, naming the file and the system-tier cure
        assert "NO LONGER READ" in result.stderr, (
            f"stale legacy env file was not announced: {result.stderr!r}"
        )
        assert str(legacy_env_file) in result.stderr
        assert "kanibako system set system.env.<VAR>=<value>" in result.stderr


# =========================================================================
# Lifecycle (start / stop)
# =========================================================================


@pytest.mark.integration
class TestKanibakoLifecycle:
    """Verify the start → running → stop → gone cycle."""

    @requires_runtime
    @pytest.mark.skip(reason="Flaky on CI: container startup timing unreliable in GitHub Actions")
    def test_start_stop_cycle(self, cli_env, container_runtime_cmd):
        """Start a container, verify it runs, stop it, verify it's gone."""
        _setup_with_image(cli_env, "busybox:latest")
        subprocess.run(
            [container_runtime_cmd, "pull", "busybox:latest"],
            capture_output=True, check=True,
        )

        # Launch kanibako in the background — the container runs `sleep`.
        proc = subprocess.Popen(
            [
                "kanibako", "start", "--ephemeral", "--entrypoint", "/bin/sleep", "--", "300",
            ],
            env=cli_env["env"],
            cwd=str(cli_env["project"]),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            # Poll until the container appears (up to 60 s).
            container_up = False
            for _ in range(60):
                time.sleep(1)
                ps = subprocess.run(
                    [container_runtime_cmd, "ps", "--format", "{{.Names}}"],
                    capture_output=True, text=True,
                )
                if any("kanibako" in name for name in ps.stdout.splitlines()):
                    container_up = True
                    break
            if not container_up:
                # Kill process to release stdout/stderr for reading.
                proc.kill()
                stdout, stderr = proc.communicate(timeout=5)
                # Also check all containers (including non-running).
                ps_all = subprocess.run(
                    [container_runtime_cmd, "ps", "-a", "--format", "{{.Names}} {{.Status}}"],
                    capture_output=True, text=True,
                )
                assert False, (
                    f"Container did not start within 60 s.\n"
                    f"Process exit code: {proc.returncode}\n"
                    f"stdout: {stdout.decode(errors='replace')}\n"
                    f"stderr: {stderr.decode(errors='replace')}\n"
                    f"All containers: {ps_all.stdout}"
                )

            # Stop via kanibako CLI.
            stop_result = _run_kanibako(
                "stop", env=cli_env["env"], cwd=str(cli_env["project"])
            )
            assert stop_result.returncode == 0

            # Verify the container is gone.
            time.sleep(2)
            ps2 = subprocess.run(
                [container_runtime_cmd, "ps", "-a", "--format", "{{.Names}}"],
                capture_output=True, text=True,
            )
            assert not any(
                "kanibako" in name for name in ps2.stdout.splitlines()
            ), f"Container still present: {ps2.stdout}"
        finally:
            proc.kill()
            proc.wait(timeout=10)

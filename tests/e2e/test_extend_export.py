"""E2E test for the extended-rig lifecycle: extend -> export -> import.

A single end-to-end test proving Increment 4 against real podman:
`rig extend e2e-rig --from oci` (driven through a pty, since extend opens an
interactive `podman run -it` shell) commits `kanibako-rig-e2e-rig` carrying
`/etc/kanibako/rig.yaml`; `rig export` bundles it to a `.rig.tgz`; the image is
removed; `rig import` restores it; `rig info` shows the extended lineage.

The foundation `--from oci` resolves to the pre-warmed `kanibako-oci:latest`
(conftest's `ensure_image_in_pinned_store`), so extend performs no build.
`--always-commit` makes the (immediately-exited) interactive shell commit
regardless of its exit status, keeping the test non-interactive.
"""

from __future__ import annotations

import os
import pty
import select
import shutil
import subprocess
import tarfile
import time

import pytest

from tests.e2e.conftest import e2e_requires, run_kanibako

pytestmark = [pytest.mark.e2e, *e2e_requires]

_podman = shutil.which("podman")

RIG_NAME = "e2e-rig"
RIG_IMAGE = "kanibako-rig-e2e-rig"
# extend opens podman run -it on the (already-prepped) oci base then commits;
# export/import save+load a ~1GB image. Give both generous budgets.
EXTEND_TIMEOUT = 180  # seconds
BUNDLE_TIMEOUT = 600  # seconds


def _run_extend_via_pty(
    args: list[str], env: dict[str, str], timeout: int
) -> tuple[int, str]:
    """Run `kanibako <args>` attached to a pty, feed an interactive exit.

    `rig extend` shells out to `podman run -it`, which needs a TTY. We give the
    kanibako subprocess a pty as its stdio (podman inherits it), then send
    `exit` + EOF so the interactive shell ends and extend proceeds to commit.
    Returns (returncode, combined_output).
    """
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        ["kanibako", *args],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        close_fds=True,
    )
    os.close(slave)
    output = b""
    sent_exit = False
    deadline = time.monotonic() + timeout
    try:
        while True:
            if time.monotonic() > deadline:
                proc.kill()
                break
            rlist, _, _ = select.select([master], [], [], 1.0)
            if rlist:
                try:
                    data = os.read(master, 4096)
                except OSError:
                    break
                if not data:
                    break
                output += data
                # Once the shell is up and has emitted something, ask it to exit.
                if not sent_exit and len(output) > 0:
                    try:
                        os.write(master, b"exit\n")
                    except OSError:
                        pass
                    sent_exit = True
            if proc.poll() is not None:
                # drain any remaining buffered output, then stop
                try:
                    while True:
                        rlist, _, _ = select.select([master], [], [], 0.2)
                        if not rlist:
                            break
                        chunk = os.read(master, 4096)
                        if not chunk:
                            break
                        output += chunk
                except OSError:
                    pass
                break
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    finally:
        os.close(master)
    return (
        proc.returncode if proc.returncode is not None else -1,
        output.decode(errors="replace"),
    )


class TestExtendExportImport:
    """extend -> export -> import roundtrip on real podman."""

    def test_extend_export_import_roundtrip(self, e2e_env):
        env = e2e_env["env"]
        tmp_path = e2e_env["tmp_path"]
        bundle = tmp_path / f"{RIG_NAME}.rig.tgz"

        assert _podman is not None, "podman required"

        def _image_exists(image: str) -> bool:
            return (
                subprocess.run(
                    [_podman, "image", "inspect", image],
                    env=env,
                    capture_output=True,
                    timeout=30,
                ).returncode
                == 0
            )

        try:
            # Clean slate: the rig image must not pre-exist.
            subprocess.run(
                [_podman, "rmi", "-f", RIG_IMAGE],
                env=env,
                capture_output=True,
                timeout=60,
            )

            # 1. EXTEND (interactive, via pty; --always-commit so the immediate
            #    shell-exit still commits).
            rc, out = _run_extend_via_pty(
                ["rig", "extend", RIG_NAME, "--from", "oci", "--always-commit"],
                env,
                EXTEND_TIMEOUT,
            )
            assert rc == 0, f"rig extend failed (rc={rc}):\n{out}"

            # 2. The extended image exists and carries /etc/kanibako/rig.yaml.
            assert _image_exists(RIG_IMAGE), f"{RIG_IMAGE} not created by extend:\n{out}"
            meta = subprocess.run(
                [
                    _podman,
                    "run",
                    "--rm",
                    "--entrypoint",
                    "sh",
                    RIG_IMAGE,
                    "-c",
                    "cat /etc/kanibako/rig.yaml",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
            assert meta.returncode == 0, f"rig.yaml missing in image:\n{meta.stderr}"
            assert "name: e2e-rig" in meta.stdout, f"unexpected rig.yaml:\n{meta.stdout}"
            assert (
                "kind: extended" in meta.stdout
            ), f"unexpected rig.yaml:\n{meta.stdout}"

            # 3. EXPORT -> a .rig.tgz containing rig.yaml + image.tar.
            res = run_kanibako(
                ["rig", "export", RIG_NAME, "--out", str(bundle)],
                env=env,
                timeout=BUNDLE_TIMEOUT,
            )
            assert (
                res.returncode == 0
            ), f"rig export failed:\n{res.stdout}\n{res.stderr}"
            assert bundle.is_file(), "export produced no bundle"
            with tarfile.open(bundle, "r:gz") as tar:
                names = set(tar.getnames())
            assert (
                "rig.yaml" in names and "image.tar" in names
            ), f"bundle members: {names}"

            # 4. Remove the image, then IMPORT it back.
            subprocess.run(
                [_podman, "rmi", "-f", RIG_IMAGE],
                env=env,
                capture_output=True,
                timeout=120,
            )
            assert not _image_exists(RIG_IMAGE), "image not removed before import"
            res = run_kanibako(
                ["rig", "import", str(bundle)],
                env=env,
                timeout=BUNDLE_TIMEOUT,
            )
            assert (
                res.returncode == 0
            ), f"rig import failed:\n{res.stdout}\n{res.stderr}"
            assert _image_exists(RIG_IMAGE), "import did not recreate the image"

            # 5. `rig info` shows extended lineage.
            info = run_kanibako(["rig", "info", RIG_NAME], env=env, timeout=60)
            assert info.returncode == 0, f"rig info failed:\n{info.stderr}"
            assert "Kind:    extended" in info.stdout, info.stdout
            assert "Status:  prepped" in info.stdout, info.stdout
            assert (
                "Parent:" in info.stdout or "Foundation:" in info.stdout
            ), info.stdout
        finally:
            subprocess.run(
                [_podman, "rmi", "-f", RIG_IMAGE],
                env=env,
                capture_output=True,
                timeout=120,
            )
            # extend's interactive container is named kanibako-extend-<name>;
            # extend removes it itself, but clean up defensively.
            subprocess.run(
                [_podman, "rm", "-f", f"kanibako-extend-{RIG_NAME}"],
                env=env,
                capture_output=True,
                timeout=30,
            )

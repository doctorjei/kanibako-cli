"""Extended tests for kanibako.runtime.container: ensure_image chain, run args, list_local_images."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kanibako.runtime.container import ContainerRuntime
from kanibako.errors import ContainerError
from kanibako.targets.base import Mount


# ---------------------------------------------------------------------------
# ensure_image chain
# ---------------------------------------------------------------------------

class TestEnsureImage:
    def test_exists_locally(self):
        rt = ContainerRuntime(command="echo")
        with patch.object(rt, "image_exists", return_value=True) as m:
            rt.ensure_image("test:latest", Path("/containers"))
            m.assert_called_once_with("test:latest")

    def test_pull_succeeds(self):
        rt = ContainerRuntime(command="echo")
        with (
            patch.object(rt, "image_exists", return_value=False),
            patch.object(rt, "pull", return_value=True) as m_pull,
        ):
            rt.ensure_image("test:latest", Path("/containers"))
            m_pull.assert_called_once_with("test:latest", quiet=False)

    def test_pull_fails_raises_actionable_no_build(self):
        """A pull failure is fatal and actionable -- no local build fallback."""
        rt = ContainerRuntime(command="echo")
        with (
            patch.object(rt, "image_exists", return_value=False),
            patch.object(rt, "pull", return_value=False),
            patch.object(rt, "build") as m_build,
        ):
            with pytest.raises(ContainerError, match="kanibako-images"):
                rt.ensure_image("kanibako-oci:latest", Path("/containers"))
            m_build.assert_not_called()

    def test_pull_failure_message_mentions_image(self):
        rt = ContainerRuntime(command="echo")
        with (
            patch.object(rt, "image_exists", return_value=False),
            patch.object(rt, "pull", return_value=False),
        ):
            with pytest.raises(ContainerError, match="kanibako-oci:latest"):
                rt.ensure_image("kanibako-oci:latest")

    def test_no_containers_dir_arg(self):
        """containers_dir is optional now (pull-only)."""
        rt = ContainerRuntime(command="echo")
        with (
            patch.object(rt, "image_exists", return_value=False),
            patch.object(rt, "pull", return_value=True) as m_pull,
        ):
            rt.ensure_image("test:latest")
            m_pull.assert_called_once_with("test:latest", quiet=False)


# ---------------------------------------------------------------------------
# run() command assembly
# ---------------------------------------------------------------------------

class TestRunCommandAssembly:
    def _make_rt(self):
        return ContainerRuntime(command="/usr/bin/podman")

    def _base_kwargs(self, tmp_path, *, vault_dirs=True):
        """Return minimal kwargs for run() using tmp_path for vault dirs."""
        vault_ro = tmp_path / "vault-ro"
        vault_rw = tmp_path / "vault-rw"
        if vault_dirs:
            vault_ro.mkdir(exist_ok=True)
            vault_rw.mkdir(exist_ok=True)
        return dict(
            shell_path=tmp_path / "home",
            project_path=tmp_path / "proj",
            vault_ro_path=vault_ro,
            vault_rw_path=vault_rw,
        )

    def test_core_mounts_not_hardwired(self, tmp_path):
        """run() NO LONGER hardwires the home/workspace/vault ``-v`` (step 3).

        The core box mounts now flow through the category resolver and arrive via
        *extra_mounts* (``start._build_core_mounts``); ``container.run`` builds NONE
        of them in-process.  Only ``-w`` (a flag, not a mount) stays hardwired.
        """
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        with patch("kanibako.runtime.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run("img:latest", **kwargs)
            cmd = m_run.call_args[0][0]
            cmd_str = " ".join(cmd)
            # The hardwired core binds are GONE (no -v built inside run for them).
            assert f"{kwargs['shell_path']}:/home/agent:Z,U" not in cmd
            assert f"{kwargs['project_path']}:/home/agent/workspace:Z,U" not in cmd
            assert "/home/agent/vault/ro:ro" not in cmd_str
            assert "/home/agent/vault/rw:Z,U" not in cmd_str
            # The working-dir flag stays.
            idx = cmd.index("-w")
            assert cmd[idx + 1] == "/home/agent/workspace"

    def test_core_mounts_arrive_via_extra_mounts(self, tmp_path):
        """The core binds ARE emitted when the caller passes them in *extra_mounts*.

        Mirrors what ``start._build_core_mounts`` hands to ``run``: the home /
        workspace / vault binds as resolved :class:`Mount`s.  ``run`` emits each as a
        plain ``-v`` via the extra-mounts loop (the ONLY remaining ``-v`` site).
        """
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        core_mounts = [
            Mount(source=kwargs["shell_path"], destination="/home/agent", options="Z,U"),
            Mount(
                source=kwargs["project_path"],
                destination="/home/agent/workspace",
                options="Z,U",
            ),
            Mount(
                source=kwargs["vault_ro_path"],
                destination="/home/agent/vault/ro",
                options="ro",
            ),
            Mount(
                source=kwargs["vault_rw_path"],
                destination="/home/agent/vault/rw",
                options="Z,U",
            ),
        ]
        with patch("kanibako.runtime.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run("img:latest", extra_mounts=core_mounts, **kwargs)
            cmd = m_run.call_args[0][0]
            assert f"{kwargs['shell_path']}:/home/agent:Z,U" in cmd
            assert f"{kwargs['project_path']}:/home/agent/workspace:Z,U" in cmd
            assert f"{kwargs['vault_ro_path']}:/home/agent/vault/ro:ro" in cmd
            assert f"{kwargs['vault_rw_path']}:/home/agent/vault/rw:Z,U" in cmd
            # The legacy box-dest is gone.
            cmd_str = " ".join(cmd)
            assert "share-ro" not in cmd_str
            assert "share-rw" not in cmd_str

    def test_vault_mounts_not_built_by_run(self, tmp_path):
        """run() builds no vault ``-v`` regardless of dirs (gate moved to caller).

        Vault gating lives entirely in ``core_defaults.core_default_categories``
        (the resolver seam), where vault is UNIVERSAL unless disabled and the source
        is created-if-missing; ``run`` never constructs a vault bind itself, so none
        appears here no matter the source state.
        """
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path, vault_dirs=False)
        with patch("kanibako.runtime.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run("img:latest", **kwargs)
            cmd_str = " ".join(m_run.call_args[0][0])
            assert "/home/agent/vault/ro" not in cmd_str
            assert "/home/agent/vault/rw" not in cmd_str

    def test_entrypoint_override(self, tmp_path):
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        with patch("kanibako.runtime.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run("img:latest", entrypoint="/bin/bash", **kwargs)
            cmd = m_run.call_args[0][0]
            idx = cmd.index("--entrypoint")
            assert cmd[idx + 1] == "/bin/bash"

    def test_no_entrypoint(self, tmp_path):
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        with patch("kanibako.runtime.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run("img:latest", **kwargs)
            cmd = m_run.call_args[0][0]
            assert "--entrypoint" not in cmd

    def test_cli_args_appended(self, tmp_path):
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        with patch("kanibako.runtime.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run("img:latest", cli_args=["--continue", "--verbose"], **kwargs)
            cmd = m_run.call_args[0][0]
            assert cmd[-2:] == ["--continue", "--verbose"]

    def test_extra_mounts(self, tmp_path):
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        mounts = [
            Mount(
                source=Path("/home/user/.local/share/claude"),
                destination="/home/agent/.local/share/claude",
                options="ro",
            ),
            Mount(
                source=Path("/home/user/.local/bin/claude"),
                destination="/home/agent/.local/bin/claude",
                options="ro",
            ),
        ]
        with patch("kanibako.runtime.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run("img:latest", extra_mounts=mounts, **kwargs)
            cmd = m_run.call_args[0][0]
            assert "/home/user/.local/share/claude:/home/agent/.local/share/claude:ro" in cmd
            assert "/home/user/.local/bin/claude:/home/agent/.local/bin/claude:ro" in cmd

    def test_no_extra_mounts(self, tmp_path):
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        with patch("kanibako.runtime.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run("img:latest", extra_mounts=None, **kwargs)
            cmd = m_run.call_args[0][0]
            cmd_str = " ".join(cmd)
            assert ".local/share/claude" not in cmd_str

    def test_cli_args_none(self, tmp_path):
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        with patch("kanibako.runtime.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run("img:latest", cli_args=None, **kwargs)
            cmd = m_run.call_args[0][0]
            # Last element should be the image name
            assert cmd[-1] == "img:latest"

    def test_container_name(self, tmp_path):
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        with patch("kanibako.runtime.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run("img:latest", name="kanibako-test", **kwargs)
            cmd = m_run.call_args[0][0]
            idx = cmd.index("--name")
            assert cmd[idx + 1] == "kanibako-test"

    def test_a_mask_is_an_empty_tmpfs_not_a_read_only_copy(self, tmp_path):
        """A mask emits ``notmpcopyup`` — the option that makes it HIDE.

        ⚑ WHAT THIS PINS, and why the option is not cosmetic: podman's tmpfs
        default is ``tmpcopyup``, which copies whatever already sits at the
        destination UP into the fresh tmpfs. Measured on real podman, the mask
        therefore showed the pre-existing content read-only and hid NOTHING. A
        mask is a void — there is nothing inside it (collapse DESIGN §8.1a). Drop
        the option and this is the only test that notices, because the difference
        is invisible to everything except what podman shows inside the box.
        """
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        with patch("kanibako.runtime.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run(
                "img:latest",
                tmpfs_masks=["/home/agent/workspace/vault"],
                **kwargs,
            )
            cmd = m_run.call_args[0][0]
            idx = cmd.index("--mount")
            assert cmd[idx + 1] == (
                "type=tmpfs,dst=/home/agent/workspace/vault,ro,notmpcopyup"
            )

    def test_tmpfs_mask_multiple(self, tmp_path):
        """Every mask gets its own EMPTY read-only tmpfs — the option is per-mount."""
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        with patch("kanibako.runtime.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run(
                "img:latest",
                tmpfs_masks=[
                    "/home/agent/workspace/vault",
                    "/home/agent/.secret",
                ],
                **kwargs,
            )
            cmd = m_run.call_args[0][0]
            assert cmd.count("--mount") == 2
            assert (
                "type=tmpfs,dst=/home/agent/workspace/vault,ro,notmpcopyup" in cmd
            )
            assert "type=tmpfs,dst=/home/agent/.secret,ro,notmpcopyup" in cmd

    def test_tmpfs_mask_empty(self, tmp_path):
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        with patch("kanibako.runtime.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run("img:latest", tmpfs_masks=None, **kwargs)
            cmd = m_run.call_args[0][0]
            assert "--mount" not in cmd

    def test_no_settings_dot_cfg_mounts(self, tmp_path):
        """Verify old-style settings_path/dot_path/cfg_file mounts are gone."""
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        with patch("kanibako.runtime.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run("img:latest", **kwargs)
            cmd = m_run.call_args[0][0]
            cmd_str = " ".join(cmd)
            # Old mounts no longer present
            assert ".kanibako:" not in cmd_str
            assert ".claude:" not in cmd_str
            assert ".claude.json:" not in cmd_str

    def test_returns_exit_code(self, tmp_path):
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        with patch("kanibako.runtime.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=42)
            rc = rt.run("img:latest", **kwargs)
            assert rc == 42

    def test_working_directory(self, tmp_path):
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        with patch("kanibako.runtime.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run("img:latest", **kwargs)
            cmd = m_run.call_args[0][0]
            idx = cmd.index("-w")
            assert cmd[idx + 1] == "/home/agent/workspace"

    def test_base_flags(self, tmp_path):
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        with patch("kanibako.runtime.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run("img:latest", **kwargs)
            cmd = m_run.call_args[0][0]
            assert cmd[0] == "/usr/bin/podman"
            assert "run" in cmd
            # -it when TTY available, -i when not (e.g. CI)
            assert "-it" in cmd or "-i" in cmd
            assert "--rm" in cmd
            # The caller must be mapped onto the image agent user (uid/gid
            # 1000) regardless of host uid: plain keep-id put a host-uid!=1000
            # caller BESIDE the agent user, and the ``:U`` binds then chowned
            # the box home + project tree to a subuid.  Literal oracle on
            # purpose — asserting via the constant would be tautological.
            assert "--userns=keep-id:uid=1000,gid=1000" in cmd
            assert "--userns=keep-id" not in cmd  # plain form must be gone


# ---------------------------------------------------------------------------
# list_local_images
# ---------------------------------------------------------------------------

class TestListLocalImages:
    def test_filters_kanibako(self):
        rt = ContainerRuntime(command="echo")
        output = (
            "ghcr.io/owner/kanibako-oci:latest\t500MB\n"
            "docker.io/library/ubuntu:latest\t100MB\n"
            "ghcr.io/owner/kanibako-lxc:latest\t800MB\n"
        )
        with patch("kanibako.runtime.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0, stdout=output)
            images = rt.list_local_images()
            assert len(images) == 2
            assert images[0][0] == "ghcr.io/owner/kanibako-oci:latest"
            assert images[1][0] == "ghcr.io/owner/kanibako-lxc:latest"

    def test_empty_output(self):
        rt = ContainerRuntime(command="echo")
        with patch("kanibako.runtime.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0, stdout="")
            images = rt.list_local_images()
            assert images == []

    def test_tab_parsing(self):
        rt = ContainerRuntime(command="echo")
        output = "ghcr.io/x/kanibako:latest\t1.2GB\n"
        with patch("kanibako.runtime.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0, stdout=output)
            images = rt.list_local_images()
            assert len(images) == 1
            assert images[0] == ("ghcr.io/x/kanibako:latest", "1.2GB")


class TestVaultDisabledRun:
    """What ``enable_vault`` does and does NOT gate in run().

    Since step 3 the vault BINDS do not come from run() at all — they flow through
    the category resolver into *extra_mounts* — so run() emits nothing vault-shaped
    either way. And ``enable_vault`` does NOT gate masks: a mask has no host source
    (which is why run() still emits it in-process rather than the caller
    pre-building it), but ``<scope>.masks`` is an ordinary user-writable key with
    nothing to do with the vault.

    ⚑ THIS CLASS USED TO PIN THE OPPOSITE, and its own docstring called that the
    intent ("``enable_vault`` gates the tmpfs masks"). It was residue, not a
    decision: when the only mask was the hardcoded ``~/workspace/vault`` tmpfs, the
    emit sat in the same block as the vault's own mounts. ``4e96daa`` routed those
    mounts out and left the wrapper behind; ``242bfde`` dropped the default mask.
    What survived was a silent drop — a vault-disabled box got NO masks and was
    told nothing — pinned here as though it were the contract."""

    def _make_rt(self):
        return ContainerRuntime(command="/usr/bin/podman")

    def test_vault_disabled_still_emits_masks(self, tmp_path):
        """A declared mask is emitted with the vault OFF; no vault mounts are.

        Re-introduce the ``if enable_vault:`` wrapper around the emit loop and
        this is the test that goes red. Every other mask test runs with the vault
        enabled, so nothing else notices.
        """
        rt = self._make_rt()
        vault_ro = tmp_path / "vault-ro"
        vault_rw = tmp_path / "vault-rw"
        vault_ro.mkdir()
        vault_rw.mkdir()
        with patch("kanibako.runtime.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run(
                "img:latest",
                shell_path=tmp_path / "home",
                project_path=tmp_path / "proj",
                vault_ro_path=vault_ro,
                vault_rw_path=vault_rw,
                tmpfs_masks=["/home/agent/workspace/vault"],
                enable_vault=False,
            )
            cmd = m_run.call_args[0][0]
            cmd_str = " ".join(cmd)
            # No vault mounts even though dirs exist (run builds none either way).
            assert "/home/agent/vault/ro" not in cmd_str
            assert "/home/agent/vault/rw" not in cmd_str
            # The declared mask IS emitted — the vault does not gate it.
            assert (
                "type=tmpfs,dst=/home/agent/workspace/vault,ro,notmpcopyup"
                in cmd_str
            )

    def test_vault_enabled_includes_tmpfs_masks(self, tmp_path):
        """The mask emit is unchanged with the vault ON — the pair to the test
        above, which pins the same output with it OFF. The vault BINDS come from
        *extra_mounts*, not run (step 3)."""
        rt = self._make_rt()
        vault_ro = tmp_path / "vault-ro"
        vault_rw = tmp_path / "vault-rw"
        vault_ro.mkdir()
        vault_rw.mkdir()
        with patch("kanibako.runtime.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run(
                "img:latest",
                shell_path=tmp_path / "home",
                project_path=tmp_path / "proj",
                vault_ro_path=vault_ro,
                vault_rw_path=vault_rw,
                tmpfs_masks=["/home/agent/workspace/vault"],
                enable_vault=True,
            )
            cmd = m_run.call_args[0][0]
            cmd_str = " ".join(cmd)
            # The tmpfs mask overlay is still emitted by run when vault is enabled.
            assert "tmpfs" in cmd_str
            assert (
                "type=tmpfs,dst=/home/agent/workspace/vault,ro,notmpcopyup"
                in cmd_str
            )
            # The vault BINDS are no longer built by run (caller routes them).
            assert "/home/agent/vault/ro:ro" not in cmd_str
            assert "/home/agent/vault/rw:Z,U" not in cmd_str

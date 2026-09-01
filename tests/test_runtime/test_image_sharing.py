"""Tests for kanibako.runtime.image_sharing module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from kanibako.runtime.image_sharing import (
    SHARED_STORE_CONTAINER_PATH,
    VIRTIOFS_GRAPHROOT_MESSAGE,
    detect_graph_root,
    generate_storage_conf,
    is_rootless_podman,
    virtiofs_graphroot_message,
    write_storage_conf,
)

#: The tail of the ``images_conf`` bind's box destination (the declarative
#: ``~/.config/containers/storage.conf`` row, resolved against the guest home).
#: Matched by SUFFIX so the assertions pin the DELIVERY, not the guest-home
#: constant.
_STORAGE_CONF_SUFFIX = ".config/containers/storage.conf"


# ---------------------------------------------------------------------------
# detect_graph_root
# ---------------------------------------------------------------------------

class TestDetectGraphRoot:
    """Tests for detect_graph_root()."""

    def test_returns_path_on_success(self, tmp_path):
        """Successfully detects graph root from podman info output."""
        graph_dir = tmp_path / "overlay"
        graph_dir.mkdir()

        with patch("kanibako.runtime.image_sharing.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=str(graph_dir) + "\n",
            )
            result = detect_graph_root("podman")
            assert result == graph_dir
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert cmd == ["podman", "info", "--format", "{{.Store.GraphRoot}}"]

    def test_returns_none_on_command_failure(self):
        """Returns None when the command fails."""
        with patch("kanibako.runtime.image_sharing.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stderr="not found",
            )
            result = detect_graph_root("podman")
            assert result is None

    def test_returns_none_on_empty_output(self):
        """Returns None when the output is empty."""
        with patch("kanibako.runtime.image_sharing.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="   \n",
            )
            result = detect_graph_root("podman")
            assert result is None

    def test_returns_none_when_directory_missing(self, tmp_path):
        """Returns None when the graph root path does not exist."""
        missing = tmp_path / "nonexistent"

        with patch("kanibako.runtime.image_sharing.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=str(missing) + "\n",
            )
            result = detect_graph_root("podman")
            assert result is None

    def test_returns_none_on_timeout(self):
        """Returns None on subprocess timeout."""
        import subprocess

        with patch("kanibako.runtime.image_sharing.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd="podman info", timeout=10,
            )
            result = detect_graph_root("podman")
            assert result is None

    def test_returns_none_on_file_not_found(self):
        """Returns None when the binary is not found."""
        with patch("kanibako.runtime.image_sharing.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("no such file")
            result = detect_graph_root("podman")
            assert result is None

    def test_uses_provided_runtime_cmd(self, tmp_path):
        """Uses the given runtime command (docker, podman path, etc.)."""
        graph_dir = tmp_path / "overlay"
        graph_dir.mkdir()

        with patch("kanibako.runtime.image_sharing.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=str(graph_dir) + "\n",
            )
            detect_graph_root("/usr/bin/docker")
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "/usr/bin/docker"


# ---------------------------------------------------------------------------
# generate_storage_conf
# ---------------------------------------------------------------------------

class TestGenerateStorageConf:
    """Tests for generate_storage_conf()."""

    def test_basic_output(self):
        """Generated config includes the additionalImageStores path."""
        conf = generate_storage_conf("/var/lib/shared-images")
        assert "[storage]" in conf
        assert 'driver = "overlay"' in conf
        assert "[storage.options]" in conf
        assert '"/var/lib/shared-images"' in conf
        assert "additionalimagestores" in conf

    def test_custom_path(self):
        """Custom path is correctly embedded."""
        conf = generate_storage_conf("/mnt/custom/store")
        assert '"/mnt/custom/store"' in conf

    def test_valid_toml_structure(self):
        """Generated output has valid TOML-like structure."""
        conf = generate_storage_conf("/test")
        lines = conf.strip().split("\n")
        assert lines[0] == "[storage]"
        assert any("[storage.options]" in line for line in lines)


# ---------------------------------------------------------------------------
# write_storage_conf (11a, 2026-08-02: the GENERATION half, split from the
# probe — the old combined prepare_image_sharing_sources coupled probe-fail to
# skipping everything, which a SET box.images_store must survive)
# ---------------------------------------------------------------------------

class TestWriteStorageConf:
    """Tests for write_storage_conf()."""

    def test_writes_conf_and_returns_path(self, tmp_path):
        """Writes the generated storage.conf into staging, returns its path."""
        staging = tmp_path / "staging"

        conf_path = write_storage_conf(staging)

        assert conf_path == staging / "storage.conf"
        # The generated conf is WRITTEN (it is the bound source, spec D-M8).
        assert conf_path.exists()
        assert SHARED_STORE_CONTAINER_PATH in conf_path.read_text()

    def test_independent_of_the_probe(self, tmp_path):
        """Generation does not consult the probe (11a: the probe feeds ONLY
        the box.images_store default; the conf content is store-independent)."""
        staging = tmp_path / "staging"

        with patch(
            "kanibako.runtime.image_sharing.detect_graph_root"
        ) as mock_detect:
            conf_path = write_storage_conf(staging)

        mock_detect.assert_not_called()
        assert conf_path.exists()


# ---------------------------------------------------------------------------
# Integration with start.py (_run_container)
# ---------------------------------------------------------------------------

class TestImageSharingInRunContainer:
    """Tests for image sharing integration in _run_container."""

    def test_share_images_flag_triggers_mounts(self, start_mocks, tmp_path):
        """--share-images adds image sharing mounts to the container.

        Phase B: the mounts now flow through the category resolver (the seam
        injects the probed graph root + generated storage.conf into keyed
        ``box.bindings.ro.images_*`` binds).  11a: the seam calls the split
        probe (``detect_graph_root``) + generation (``write_storage_conf``);
        patch both to real paths so the reconciled ro mount keeps its existing
        source (not dropped by L7).  The PROBED-DEFAULT arm — unchanged by 11a.
        """
        from kanibako.commands.start import _run_container

        graph_root = tmp_path / "graph"
        graph_root.mkdir()
        conf = tmp_path / "storage.conf"
        conf.write_text("[storage]\n")

        with start_mocks() as m:
            # ⚑ The seam gate reads ``merged.box_share_images`` ALONE (B6) and
            # this fixture MOCKS ``load_merged_config``, so the ``--share-images``
            # ride through the §1A CLI level cannot reach it here — set the value
            # that resolve would have produced.  (The flag→key transport itself is
            # pinned by tests/test_settings/test_config.py.)
            m.merged.box_share_images = True
            with (
                patch(
                    "kanibako.runtime.image_sharing.detect_graph_root",
                    return_value=graph_root,
                ) as mock_detect,
                patch(
                    "kanibako.runtime.image_sharing.write_storage_conf",
                    return_value=conf,
                ) as mock_write,
            ):
                rc = _run_container(
                    project_dir=None,
                    entrypoint=None,
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                    share_images=True,
                )
                assert rc == 0
                mock_detect.assert_called_once()
                mock_write.assert_called_once()
                # Verify the mount was included in the runtime.run call
                call_kwargs = m.runtime.run.call_args.kwargs
                extra = call_kwargs.get("extra_mounts") or []
                destinations = [mt.destination for mt in extra]
                assert SHARED_STORE_CONTAINER_PATH in destinations
                # The store bind's SOURCE is the probed default (the resolved
                # ``box.images_store`` with no set tier value).
                by_dest = {mt.destination: mt for mt in extra}
                assert by_dest[SHARED_STORE_CONTAINER_PATH].source == graph_root
                # ... and the GENERATED storage.conf rides with it.
                conf_mounts = [
                    mt for mt in extra
                    if str(mt.destination).endswith(_STORAGE_CONF_SUFFIX)
                ]
                assert [mt.source for mt in conf_mounts] == [conf]

    def test_share_images_disabled_by_default(self, start_mocks):
        """Without --share-images, the probe is never even run."""
        from kanibako.commands.start import _run_container

        with start_mocks():
            with patch(
                "kanibako.runtime.image_sharing.detect_graph_root",
            ) as mock_detect:
                _run_container(
                    project_dir=None,
                    entrypoint=None,
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                )
                mock_detect.assert_not_called()

    def test_share_images_probe_fail_unset_warns(
        self, start_mocks, tmp_path, capsys,
    ):
        """Probe-fail + NO set box.images_store: skip WITH the 11a warning.

        The warning must NAME the cause (the probe failed AND no
        ``box.images_store`` is set) and the cure (set the key or fix podman)
        — the pre-11a silent-cause warning named only "could not be detected".
        Launch continues without sharing.
        """
        from kanibako.commands.start import _run_container

        box_meta = tmp_path / "box-meta"
        box_meta.mkdir()

        with start_mocks() as m:
            # A REAL metadata path: the staging storage.conf is genuinely
            # written; the box-tier box.yaml is absent (no set value).
            m.proj.metadata_path = box_meta
            m.merged.box_share_images = True  # the B6 gate (see above)
            with patch(
                "kanibako.runtime.image_sharing.detect_graph_root",
                return_value=None,  # the probe failed
            ):
                rc = _run_container(
                    project_dir=None,
                    entrypoint=None,
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                    share_images=True,
                )
                assert rc == 0  # continues without image sharing
                # Nothing image-flavored was mounted — NEITHER the store bind
                # NOR the generated storage.conf (its DELIVERY follows the
                # resolved store: no store, nothing delivered).
                call_kwargs = m.runtime.run.call_args.kwargs
                extra = call_kwargs.get("extra_mounts") or []
                dests = [str(mt.destination) for mt in extra]
                assert SHARED_STORE_CONTAINER_PATH not in dests
                assert not [d for d in dests if d.endswith(_STORAGE_CONF_SUFFIX)]

        captured = capsys.readouterr()
        assert "Warning" in captured.err
        assert "probe failed" in captured.err
        assert "no box.images_store is set" in captured.err
        assert "set box.images_store" in captured.err
        assert "Continuing without image sharing" in captured.err

    def test_share_images_set_store_shares_on_probe_fail(
        self, start_mocks, tmp_path, capsys,
    ):
        """Ruled 11a: probe-fail + a SET box.images_store still shares.

        The probe feeds ONLY the key's DEFAULT; the RESOLVED key (here the
        box-tier file value) feeds the bind — so a failed probe with a set
        store emits the mounts FROM THE SET STORE, with no warning.
        """
        from kanibako.commands.start import _run_container
        from kanibako.settings.config import BOX_META_FILE

        store_dir = tmp_path / "host-store"
        store_dir.mkdir()
        box_meta = tmp_path / "box-meta"
        box_meta.mkdir()
        (box_meta / BOX_META_FILE).write_text(
            f"box:\n  images_store: {store_dir}\n"
        )

        with start_mocks() as m:
            m.proj.metadata_path = box_meta
            m.merged.box_share_images = True  # the B6 gate (see above)
            with patch(
                "kanibako.runtime.image_sharing.detect_graph_root",
                return_value=None,  # the probe failed
            ):
                rc = _run_container(
                    project_dir=None,
                    entrypoint=None,
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                    share_images=True,
                )
                assert rc == 0
                call_kwargs = m.runtime.run.call_args.kwargs
                extra = call_kwargs.get("extra_mounts") or []
                by_dest = {mt.destination: mt for mt in extra}
                # The store bind emitted FROM THE SET VALUE.
                assert SHARED_STORE_CONTAINER_PATH in by_dest
                assert by_dest[SHARED_STORE_CONTAINER_PATH].source == store_dir
                # The generated storage.conf was written for real AND delivered
                # alongside the resolved store (the ``images_conf`` internal
                # bind — it follows the store, not the probe).
                staged_conf = box_meta / ".image-sharing" / "storage.conf"
                assert staged_conf.exists()
                conf_mounts = [
                    mt for mt in extra
                    if str(mt.destination).endswith(_STORAGE_CONF_SUFFIX)
                ]
                assert [mt.source for mt in conf_mounts] == [staged_conf]

        captured = capsys.readouterr()
        assert "Continuing without image sharing" not in captured.err

    def test_share_images_via_config(self, start_mocks, tmp_path):
        """share_images=true in config triggers image sharing source resolution.

        The gate is the RESOLVED ``box.share_images`` (B6), so a stored value
        drives the seam with no flag at all.
        """
        from kanibako.commands.start import _run_container

        box_meta = tmp_path / "box-meta"
        box_meta.mkdir()

        with start_mocks() as m:
            m.proj.metadata_path = box_meta
            m.merged.box_share_images = True
            with patch(
                "kanibako.runtime.image_sharing.detect_graph_root",
                return_value=None,
            ) as mock_detect:
                _run_container(
                    project_dir=None,
                    entrypoint=None,
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                    share_images=False,
                )
                mock_detect.assert_called_once()

    def test_share_images_in_shell_mode(self, start_mocks, tmp_path):
        """--share-images works in shell mode too."""
        from kanibako.commands.start import _run_container

        box_meta = tmp_path / "box-meta"
        box_meta.mkdir()

        with start_mocks() as m:
            m.proj.metadata_path = box_meta
            m.merged.box_share_images = True  # the B6 gate (see above)
            with patch(
                "kanibako.runtime.image_sharing.detect_graph_root",
                return_value=None,
            ) as mock_detect:
                _run_container(
                    project_dir=None,
                    entrypoint="/bin/bash",
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                    share_images=True,
                )
                mock_detect.assert_called_once()


# ---------------------------------------------------------------------------
# is_rootless_podman
# ---------------------------------------------------------------------------

class TestIsRootlessPodman:
    """Tests for is_rootless_podman()."""

    def test_true_for_rootless_podman(self):
        with patch("kanibako.runtime.image_sharing.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="true\n")
            assert is_rootless_podman("podman") is True
            cmd = mock_run.call_args[0][0]
            assert cmd == [
                "podman", "info", "--format", "{{.Host.Security.Rootless}}",
            ]

    def test_false_for_rootful_podman(self):
        with patch("kanibako.runtime.image_sharing.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="false\n")
            assert is_rootless_podman("/usr/bin/podman") is False

    def test_false_for_docker_without_query(self):
        """docker is never the rootless-overlay case; no info call is made."""
        with patch("kanibako.runtime.image_sharing.subprocess.run") as mock_run:
            assert is_rootless_podman("/usr/bin/docker") is False
            mock_run.assert_not_called()

    def test_none_on_command_failure(self):
        with patch("kanibako.runtime.image_sharing.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="boom")
            assert is_rootless_podman("podman") is None

    def test_none_on_unparseable_output(self):
        with patch("kanibako.runtime.image_sharing.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="maybe\n")
            assert is_rootless_podman("podman") is None

    def test_none_on_oserror(self):
        with patch("kanibako.runtime.image_sharing.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("nope")
            assert is_rootless_podman("podman") is None


# ---------------------------------------------------------------------------
# path_fstype
# ---------------------------------------------------------------------------

class TestPathFstype:
    """Tests for path_fstype() (procfs-based fstype lookup)."""

    _MOUNTINFO = (
        "21 0 0:20 / / rw - ext4 /dev/root rw\n"
        "22 21 0:21 / /mnt/share rw - virtiofs myfs rw\n"
    )

    def test_returns_fstype_of_longest_match(self, tmp_path):
        from kanibako.runtime.image_sharing import path_fstype
        from unittest.mock import mock_open

        with patch("kanibako.runtime.image_sharing.os.path.realpath",
                   return_value="/mnt/share/overlay"):
            with patch("builtins.open", mock_open(read_data=self._MOUNTINFO)):
                assert path_fstype(Path("/mnt/share/overlay")) == "virtiofs"

    def test_returns_root_fstype_for_unmatched_subpath(self):
        from kanibako.runtime.image_sharing import path_fstype
        from unittest.mock import mock_open

        with patch("kanibako.runtime.image_sharing.os.path.realpath",
                   return_value="/home/agent/.local/share"):
            with patch("builtins.open", mock_open(read_data=self._MOUNTINFO)):
                assert path_fstype(Path("/home/agent/.local/share")) == "ext4"

    def test_none_when_mountinfo_unreadable(self):
        from kanibako.runtime.image_sharing import path_fstype

        with patch("kanibako.runtime.image_sharing.os.path.realpath",
                   return_value="/x"):
            with patch("builtins.open", side_effect=OSError("no procfs")):
                assert path_fstype(Path("/x")) is None


# ---------------------------------------------------------------------------
# virtiofs_graphroot_message
# ---------------------------------------------------------------------------

class TestVirtiofsGraphrootMessage:
    """Tests for the rootless-podman-on-virtiofs preflight diagnostic."""

    def _patches(self, *, rootless, graph_root, fstype, docker_cmd=None):
        env = {} if docker_cmd is None else {"KANIBAKO_DOCKER_CMD": docker_cmd}
        return (
            patch.dict("os.environ", env, clear=False),
            patch(
                "kanibako.runtime.image_sharing.is_rootless_podman",
                return_value=rootless,
            ),
            patch(
                "kanibako.runtime.image_sharing.detect_graph_root",
                return_value=graph_root,
            ),
            patch(
                "kanibako.runtime.image_sharing.path_fstype",
                return_value=fstype,
            ),
        )

    def test_fires_for_rootless_virtiofs(self, tmp_path):
        """Rootless podman + virtiofs graph root -> the diagnostic message."""
        gr = tmp_path / "overlay"
        p_env, p_root, p_gr, p_fs = self._patches(
            rootless=True, graph_root=gr, fstype="virtiofs",
        )
        with p_env, p_root, p_gr, p_fs:
            msg = virtiofs_graphroot_message("podman")
        assert msg is not None
        assert "virtiofs" in msg
        assert "kento" in msg
        assert "pivot_root" in msg
        assert "KANIBAKO_DOCKER_CMD" in msg
        assert "unsupported" in msg
        assert str(gr) in msg

    def test_silent_for_non_virtiofs(self, tmp_path):
        """A normal (ext4) graph root -> no message."""
        p_env, p_root, p_gr, p_fs = self._patches(
            rootless=True, graph_root=tmp_path, fstype="ext4",
        )
        with p_env, p_root, p_gr, p_fs:
            assert virtiofs_graphroot_message("podman") is None

    def test_silent_for_rootful(self, tmp_path):
        """Rootful podman handles virtiofs fine -> no message."""
        p_env, p_root, p_gr, p_fs = self._patches(
            rootless=False, graph_root=tmp_path, fstype="virtiofs",
        )
        with p_env, p_root, p_gr, p_fs:
            assert virtiofs_graphroot_message("podman") is None

    def test_silent_when_rootless_unknown(self, tmp_path):
        """Rootless state undeterminable -> no message (no false positive)."""
        p_env, p_root, p_gr, p_fs = self._patches(
            rootless=None, graph_root=tmp_path, fstype="virtiofs",
        )
        with p_env, p_root, p_gr, p_fs:
            assert virtiofs_graphroot_message("podman") is None

    def test_silent_when_shim_active(self, tmp_path):
        """A KANIBAKO_DOCKER_CMD rootful shim suppresses the check entirely."""
        p_env, p_root, p_gr, p_fs = self._patches(
            rootless=True, graph_root=tmp_path, fstype="virtiofs",
            docker_cmd="/usr/bin/sudo-podman",
        )
        with p_env, p_root, p_gr, p_fs:
            assert virtiofs_graphroot_message("podman") is None

    def test_silent_when_graph_root_undetectable(self):
        """No detectable graph root -> no message."""
        p_env, p_root, p_gr, p_fs = self._patches(
            rootless=True, graph_root=None, fstype="virtiofs",
        )
        with p_env, p_root, p_gr, p_fs:
            assert virtiofs_graphroot_message("podman") is None

    def test_silent_when_fstype_undeterminable(self, tmp_path):
        """fstype undeterminable -> no message, no crash."""
        p_env, p_root, p_gr, p_fs = self._patches(
            rootless=True, graph_root=tmp_path, fstype=None,
        )
        with p_env, p_root, p_gr, p_fs:
            assert virtiofs_graphroot_message("podman") is None

    def test_message_constant_mentions_kento_and_virtiofs(self):
        """The message template names the load-bearing concepts."""
        assert "virtiofs" in VIRTIOFS_GRAPHROOT_MESSAGE
        assert "kento" in VIRTIOFS_GRAPHROOT_MESSAGE
        assert "{graph_root}" in VIRTIOFS_GRAPHROOT_MESSAGE


# ---------------------------------------------------------------------------
# Preflight integration with start.py (_run_container)
# ---------------------------------------------------------------------------

class TestVirtiofsPreflightInRunContainer:
    """Tests for the virtiofs preflight wired into _run_container."""

    def test_preflight_blocks_launch(self, start_mocks, capsys):
        """When the diagnostic applies, the launch is blocked (rc 1)."""
        from kanibako.commands.start import _run_container

        with start_mocks() as m:
            m.virtiofs_check.return_value = (
                "Error: podman's image storage is on a virtiofs filesystem.\n"
                "  This can happen if you use kento to compose VM images."
            )
            rc = _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            assert rc == 1
            m.runtime.run.assert_not_called()

        captured = capsys.readouterr()
        assert "virtiofs" in captured.err
        assert "kento" in captured.err

    def test_no_preflight_allows_launch(self, start_mocks):
        """When the diagnostic does not apply, the launch proceeds normally."""
        from kanibako.commands.start import _run_container

        with start_mocks() as m:
            m.virtiofs_check.return_value = None
            rc = _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            assert rc == 0
            m.runtime.run.assert_called_once()


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------

class TestImageSharingConfig:
    """Tests for the ``box.share_images`` SETTINGS key.

    ⚑⚑ THE FIXTURE FILE AND THE CLAIM MOVED HERE ON 2026-08-31 (R153), NOT THE
    BEHAVIOUR.  Every case below wrote its ``box:`` table into a path named
    ``kanibako_config.yaml`` and read it back with ``load_config`` — green, because that
    was a GENERAL document reader.  Spec §1 gives the Layer-1 bootstrap file the
    ``config.*`` paths ALONE, so the key's file is a SETTINGS file and its reader is
    ``load_merged_config``.  The Layer-1 positional is the EMPTY file the ``config_file``
    fixture writes, which also isolates the XDG env the box-scalar resolve reads.

    ⚑ THE CASCADE-PRECEDENCE CLAIM IS NOT RE-ASSERTED HERE.  It lives, over a lower-tier
    floor that DIFFERS from its declared default, at ``tests/test_settings/test_config.py
    ::TestMergedConfigKeyspaceResolve::test_box_tier_beats_workset_beats_global``
    (``box.image``: ``ws-img:2`` witnessed live, then ``box-img:3`` winning).  A version
    of it on ``box.share_images`` cannot fail: ``false`` IS this key's declared default
    (spec §2b), so a workset tier holding ``false`` is indistinguishable from no workset
    tier at all.  What belongs here is this key's own behaviour as a real bool — and the
    ``true``-floor case below carries the precedence anyway, in the one direction where
    the assertion can tell the tiers apart.
    """

    def test_default_is_false(self):
        """box_share_images defaults to False in KanibakoConfig."""
        from kanibako.settings.config import KanibakoConfig
        cfg = KanibakoConfig()
        assert cfg.box_share_images is False

    def test_loaded_from_a_settings_file(self, config_file, tmp_path):
        """``box.share_images: true`` in the BOX-tier settings file reaches the merged object."""
        from kanibako.settings.config import BOX_META_FILE, load_merged_config

        box_settings = tmp_path / BOX_META_FILE
        box_settings.write_text("box:\n  share_images: true\n")
        cfg = load_merged_config(config_file, box_settings)
        assert cfg.box_share_images is True

    def test_false_in_a_settings_file(self, config_file, tmp_path):
        """A stored ``false`` is a real value — it beats a ``true`` at the tier below.

        ⚑ THE ``true`` FLOOR IS THE POINT (P15).  ``False`` is also the declared
        default, so a bare "reads back as False" case passes with the file ignored
        outright AND with the stored ``false`` mis-coerced to the truthy string
        ``"False"`` — the exact defect ``_typed_box_scalar``'s bool arm exists to stop.
        Against a ``true`` floor, both of those RED.  ⚑ It is also the ONE direction in
        which this key can witness box-over-workset at all (class docstring).
        """
        from kanibako.settings.config import (
            BOX_META_FILE,
            WORKSET_META_FILE,
            load_merged_config,
        )

        workset_settings = tmp_path / WORKSET_META_FILE
        workset_settings.write_text("box:\n  share_images: true\n")
        # ⚑ SELF-EMPTINESS GUARD (P15): the floor has to be LIVE, or the ``False``
        # below is just the declared default and this case witnesses nothing.
        assert load_merged_config(
            config_file, workset_path=workset_settings,
        ).box_share_images is True

        box_settings = tmp_path / BOX_META_FILE
        box_settings.write_text("box:\n  share_images: false\n")
        cfg = load_merged_config(
            config_file, box_settings, workset_path=workset_settings,
        )
        assert cfg.box_share_images is False

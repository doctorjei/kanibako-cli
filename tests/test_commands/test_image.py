"""Tests for kanibako.commands.image."""

from __future__ import annotations

import argparse
import json
from unittest.mock import MagicMock, patch

import pytest

from kanibako.commands.image import _extract_ghcr_owner, _list_remote_packages
from kanibako.config import load_config
from kanibako.paths import load_std_paths


class TestImage:
    def test_runs_without_error(self, config_file, tmp_home, credentials_dir, capsys):
        """Smoke test: image list runs without crashing."""
        from kanibako.commands.image import run_list

        args = argparse.Namespace(quiet=False, as_json=False)
        rc = run_list(args)
        assert rc == 0

        captured = capsys.readouterr()
        assert "Prefabs" in captured.out
        assert "Current rig:" in captured.out

    def test_quiet_mode(self, config_file, tmp_home, credentials_dir, capsys):
        """Quiet mode prints only image names."""
        from kanibako.commands.image import run_list

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.list_local_images.return_value = [
                ("kanibako-oci:latest", "1 GB"),
                ("kanibako-lxc:latest", "2 GB"),
            ]
            MockRT.return_value = runtime

            args = argparse.Namespace(quiet=True)
            rc = run_list(args)
            assert rc == 0

        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")
        assert lines == ["kanibako-oci:latest", "kanibako-lxc:latest"]
        # Should NOT contain section headers
        assert "Built-in" not in captured.out
        assert "Current rig:" not in captured.out

    def test_quiet_mode_no_runtime(self, config_file, tmp_home, credentials_dir, capsys):
        """Quiet mode with no runtime returns empty output."""
        from kanibako.commands.image import run_list
        from kanibako.errors import ContainerError

        with patch("kanibako.commands.image.ContainerRuntime", side_effect=ContainerError("none")):
            args = argparse.Namespace(quiet=True)
            rc = run_list(args)
            assert rc == 0

        captured = capsys.readouterr()
        assert captured.out.strip() == ""

    def test_groups_by_kind(self, config_file, tmp_home, credentials_dir, capsys):
        """list groups rigs under Prefabs / Templates / Extended headings."""
        from kanibako.commands.image import run_list

        args = argparse.Namespace(quiet=False, as_json=False)
        rc = run_list(args)
        assert rc == 0

        out = capsys.readouterr().out
        assert "Prefabs (pull to prep):" in out
        assert "Templates (build to prep):" in out
        assert "Extended (interactive; export/import to move):" in out

        prefabs_part, _, rest = out.partition("Templates (build to prep):")
        templates_part, _, extended_part = rest.partition(
            "Extended (interactive; export/import to move):"
        )
        # Known base prefabs land under Prefabs.
        assert "oci" in prefabs_part
        # Bundled templates land under Templates, tagged [bundled].
        assert "jvm" in templates_part
        assert "[bundled]" in templates_part

    def test_derived_status_prepped_vs_unprepped(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        """Status is derived live from the local store, not a stored field."""
        from kanibako.commands.image import run_list

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            # Only kanibako-oci is present locally; everything else is absent.
            runtime.image_exists.side_effect = lambda img: img == "kanibako-oci:latest"
            runtime.image_inspect.return_value = None
            runtime.list_local_images.return_value = []
            MockRT.return_value = runtime

            args = argparse.Namespace(quiet=False, as_json=False)
            rc = run_list(args)
            assert rc == 0

        out = capsys.readouterr().out
        prefabs_part, _, _ = out.partition("Templates (build to prep):")
        lines = prefabs_part.splitlines()
        oci_line = next(line for line in lines if line.strip().startswith("oci"))
        assert "prepped" in oci_line
        min_line = next(line for line in lines if line.strip().startswith("min"))
        assert "unprepped" in min_line

    def test_extended_local_image_listed(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        """A local kanibako-rig-* image appears under Extended as prepped."""
        from kanibako.commands.image import run_list

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.image_exists.return_value = False
            runtime.image_inspect.return_value = None
            runtime.list_local_images.return_value = [
                ("kanibako-rig-foo:latest", "1 GB"),
            ]
            MockRT.return_value = runtime

            args = argparse.Namespace(quiet=False, as_json=False)
            rc = run_list(args)
            assert rc == 0

        out = capsys.readouterr().out
        _, _, extended_part = out.partition(
            "Extended (interactive; export/import to move):"
        )
        foo_line = next(
            line for line in extended_part.splitlines() if line.strip().startswith("foo")
        )
        assert "prepped" in foo_line

    def test_json_output(self, config_file, tmp_home, credentials_dir, capsys):
        """--json emits a parseable document with the expected groups."""
        from kanibako.commands.image import run_list

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.image_exists.side_effect = lambda img: img == "kanibako-oci:latest"
            runtime.image_inspect.return_value = None
            runtime.list_local_images.return_value = []
            MockRT.return_value = runtime

            args = argparse.Namespace(quiet=False, as_json=True)
            rc = run_list(args)
            assert rc == 0

        data = json.loads(capsys.readouterr().out)
        assert set(data) == {"prefabs", "templates", "extended", "current"}
        oci = next(p for p in data["prefabs"] if p["name"] == "oci")
        assert oci["status"] == "prepped"
        min_ = next(p for p in data["prefabs"] if p["name"] == "min")
        assert min_["status"] == "unprepped"


class TestImageInfo:
    def test_info_shows_details(self, config_file, tmp_home, credentials_dir, capsys):
        """image info displays kind, status, and image metadata when prepped."""
        from kanibako.commands.image import run_info

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            # Only the prefab ref exists -> resolves as a prepped prefab (not a
            # template/extended candidate); inspect then runs.
            runtime.image_exists.side_effect = lambda img: img == "kanibako-oci:latest"
            runtime.image_inspect.return_value = {
                "Id": "sha256:abc123def456789",
                "Created": "2026-03-01T10:00:00Z",
                "Size": 1_500_000_000,
                "Labels": {"org.example.key": "value"},
            }
            MockRT.return_value = runtime

            args = argparse.Namespace(image="kanibako-oci")
            rc = run_info(args)
            assert rc == 0

        captured = capsys.readouterr()
        assert "Name:" in captured.out
        assert "Kind:    prefab" in captured.out
        assert "Status:  prepped" in captured.out
        assert "ID:" in captured.out
        assert "Created:" in captured.out
        assert "1.5 GB" in captured.out
        assert "org.example.key=value" in captured.out

    def test_info_not_found(self, config_file, tmp_home, credentials_dir, capsys):
        """image info returns 1 for an unknown speculative prefab name."""
        from kanibako.commands.image import run_info

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.image_exists.return_value = False
            runtime.image_inspect.return_value = None
            runtime.list_local_images.return_value = []
            MockRT.return_value = runtime

            args = argparse.Namespace(image="nosuch")
            rc = run_info(args)
            assert rc == 1

        captured = capsys.readouterr()
        assert "not found" in captured.err

    def test_info_size_mb(self, config_file, tmp_home, credentials_dir, capsys):
        """image info displays MB for smaller images."""
        from kanibako.commands.image import run_info

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.image_exists.return_value = True
            runtime.image_inspect.return_value = {
                "Id": "sha256:abc",
                "Size": 500_000_000,
            }
            MockRT.return_value = runtime

            args = argparse.Namespace(image="kanibako-oci")
            rc = run_info(args)
            assert rc == 0

        captured = capsys.readouterr()
        assert "500.0 MB" in captured.out

    def test_info_no_labels(self, config_file, tmp_home, credentials_dir, capsys):
        """image info works without labels."""
        from kanibako.commands.image import run_info

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.image_exists.return_value = True
            runtime.image_inspect.return_value = {
                "Id": "sha256:abc",
            }
            MockRT.return_value = runtime

            args = argparse.Namespace(image="kanibako-oci")
            rc = run_info(args)
            assert rc == 0

        captured = capsys.readouterr()
        assert "Labels" not in captured.out

    def test_info_template_unprepped_shows_provenance(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        """An unprepped bundled template shows Kind/Status + Containerfile/Checks."""
        from kanibako.commands.image import run_info

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.image_exists.return_value = False  # not built -> unprepped
            runtime.image_inspect.return_value = None
            runtime.list_local_images.return_value = []
            MockRT.return_value = runtime

            args = argparse.Namespace(image="jvm")
            rc = run_info(args)
            assert rc == 0

        out = capsys.readouterr().out
        assert "Kind:    template" in out
        assert "Status:  unprepped" in out
        assert "Containerfile:" in out
        assert "Checks:" in out
        assert "java -version" in out


class TestImageRm:
    def test_rm_with_force(self, config_file, tmp_home, credentials_dir, capsys):
        """image rm --force removes without prompting."""
        from kanibako.commands.image import run_rm

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            MockRT.return_value = runtime

            args = argparse.Namespace(image="kanibako-oci", force=True)
            rc = run_rm(args)
            assert rc == 0

            runtime.remove_image.assert_called_once()

    def test_rm_confirmed(self, config_file, tmp_home, credentials_dir, capsys, monkeypatch):
        """image rm prompts and proceeds on 'y'."""
        from kanibako.commands.image import run_rm

        monkeypatch.setattr("builtins.input", lambda _: "y")

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            MockRT.return_value = runtime

            args = argparse.Namespace(image="kanibako-oci", force=False)
            rc = run_rm(args)
            assert rc == 0
            runtime.remove_image.assert_called_once()

    def test_rm_cancelled(self, config_file, tmp_home, credentials_dir, capsys, monkeypatch):
        """image rm prompts and cancels on 'n'."""
        from kanibako.commands.image import run_rm

        monkeypatch.setattr("builtins.input", lambda _: "n")

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            MockRT.return_value = runtime

            args = argparse.Namespace(image="kanibako-oci", force=False)
            rc = run_rm(args)
            assert rc == 0
            runtime.remove_image.assert_not_called()

        captured = capsys.readouterr()
        assert "Cancelled" in captured.out

    def test_rm_template_warns_local_only(self, config_file, tmp_home, credentials_dir, capsys, monkeypatch):
        """image rm warns that template images are not recoverable."""
        from kanibako.commands.image import run_rm

        monkeypatch.setattr("builtins.input", lambda _: "y")

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            MockRT.return_value = runtime

            args = argparse.Namespace(image="kanibako-template-jvm", force=False)
            rc = run_rm(args)
            assert rc == 0

        captured = capsys.readouterr()
        assert "not recoverable" in captured.out

    def test_rm_non_template_hints_rebuild(self, config_file, tmp_home, credentials_dir, capsys, monkeypatch):
        """image rm hints at rebuild for registry-backed images."""
        from kanibako.commands.image import run_rm

        monkeypatch.setattr("builtins.input", lambda _: "y")

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            MockRT.return_value = runtime

            args = argparse.Namespace(image="kanibako-oci", force=False)
            rc = run_rm(args)
            assert rc == 0

        captured = capsys.readouterr()
        assert "recoverable" in captured.out

    def test_rm_failure(self, config_file, tmp_home, credentials_dir, capsys):
        """image rm returns 1 on remove failure."""
        from kanibako.commands.image import run_rm
        from kanibako.errors import ContainerError

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.remove_image.side_effect = ContainerError("no such image")
            MockRT.return_value = runtime

            args = argparse.Namespace(image="nosuch", force=True)
            rc = run_rm(args)
            assert rc == 1


class TestImageRebuild:
    def test_pull_one_success(self, tmp_home, config_file, credentials_dir, capsys):
        """Default rebuild pulls from registry when no Containerfile matches."""
        from kanibako.commands.image import run_rebuild

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.pull.return_value = True
            runtime.guess_containerfile.return_value = None
            MockRT.return_value = runtime

            args = argparse.Namespace(
                image="ghcr.io/foo/kanibako-oci:latest",
                all_images=False,
            )
            rc = run_rebuild(args)
            assert rc == 0
            runtime.pull.assert_called_once()
            runtime.rebuild.assert_not_called()

        # rebuild still works but emits a deprecation notice.
        assert "note: 'rig rebuild' is deprecated" in capsys.readouterr().err

    def test_local_build_one_success(self, tmp_home, config_file, credentials_dir, capsys):
        """Auto-detect triggers local build when Containerfile matches."""
        from kanibako.commands.image import run_rebuild

        config = load_config(config_file)
        std = load_std_paths(config)
        containers_dir = std.data_path / "containers"
        containers_dir.mkdir(parents=True, exist_ok=True)
        (containers_dir / "Containerfile.kanibako").write_text("FROM ubuntu\n")

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.guess_containerfile.return_value = "kanibako"
            runtime.get_base_image.return_value = "ghcr.io/doctorjei/droste-fiber:latest"
            runtime.get_variant.return_value = "oci"
            runtime.rebuild.return_value = 0
            MockRT.return_value = runtime

            args = argparse.Namespace(
                image="kanibako-oci:latest",
                all_images=False,
            )
            rc = run_rebuild(args)
            assert rc == 0
            runtime.rebuild.assert_called_once()
            # Verify build_args passed
            call_kwargs = runtime.rebuild.call_args
            assert call_kwargs[1]["build_args"] == {
                "BASE_IMAGE": "ghcr.io/doctorjei/droste-fiber:latest",
                "VARIANT": "oci",
            }

    def test_local_build_unknown_image(self, tmp_home, config_file, credentials_dir, capsys):
        """Unknown image pattern falls back to pull."""
        from kanibako.commands.image import run_rebuild

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.guess_containerfile.return_value = None
            runtime.pull.return_value = True
            MockRT.return_value = runtime

            args = argparse.Namespace(
                image="unknown:latest",
                all_images=False,
            )
            rc = run_rebuild(args)
            assert rc == 0
            # Should fall back to pull since no Containerfile found
            runtime.pull.assert_called_once()

    def test_pull_all(self, tmp_home, config_file, credentials_dir, capsys):
        """--all updates all local images."""
        from kanibako.commands.image import run_rebuild

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.list_local_images.return_value = [
                ("ghcr.io/foo/kanibako-oci:latest", "1GB"),
                ("ghcr.io/foo/kanibako-lxc:latest", "2GB"),
            ]
            runtime.pull.return_value = True
            runtime.guess_containerfile.return_value = None
            MockRT.return_value = runtime

            args = argparse.Namespace(
                image=None, all_images=True,
            )
            rc = run_rebuild(args)
            assert rc == 0
            assert runtime.pull.call_count == 2


class TestImagePrep:
    def _args(self, name=None, force=False, all_images=False):
        return argparse.Namespace(name=name, force=force, all_images=all_images)

    def _resolution(self, **kw):
        from kanibako.rig_resolve import RigResolution

        return RigResolution(**kw)

    def test_prep_template_builds(self, tmp_home, config_file, credentials_dir, capsys):
        """prep on a template name builds the template image and returns rc."""
        from kanibako.commands.image import run_prep
        from pathlib import Path

        cf = Path("/somewhere/Containerfile.template-jvm")
        res = self._resolution(
            name="jvm", kind="template", image="kanibako-template-jvm",
            prep_action="build", containerfile=cf,
        )
        with patch("kanibako.commands.image.ContainerRuntime") as MockRT, \
                patch("kanibako.commands.image.resolve_rig", return_value=res):
            runtime = MagicMock()
            runtime.rebuild.return_value = 0
            MockRT.return_value = runtime

            rc = run_prep(self._args(name="jvm"))
            assert rc == 0
            runtime.rebuild.assert_called_once()
            call_args, call_kwargs = runtime.rebuild.call_args
            assert call_args[0] == "kanibako-template-jvm"
            assert call_args[1] == cf
            assert call_kwargs["build_args"] is None
            runtime.pull.assert_not_called()

        out = capsys.readouterr().out
        assert "prepped as kanibako-template-jvm" in out

    def test_prep_template_build_failure_returns_rc(self, tmp_home, config_file, credentials_dir, capsys):
        """A failed template build surfaces the non-zero rc."""
        from kanibako.commands.image import run_prep
        from pathlib import Path

        res = self._resolution(
            name="jvm", kind="template", image="kanibako-template-jvm",
            prep_action="build", containerfile=Path("/x/Containerfile.template-jvm"),
        )
        with patch("kanibako.commands.image.ContainerRuntime") as MockRT, \
                patch("kanibako.commands.image.resolve_rig", return_value=res):
            runtime = MagicMock()
            runtime.rebuild.return_value = 2
            MockRT.return_value = runtime

            rc = run_prep(self._args(name="jvm"))
            assert rc == 2

        assert "Build failed" in capsys.readouterr().err

    def test_prep_prefab_pulls(self, tmp_home, config_file, credentials_dir, capsys):
        """prep on a not-local prefab goes through _update_one -> pull."""
        from kanibako.commands.image import run_prep

        res = self._resolution(
            name="oci", kind="prefab",
            image="ghcr.io/doctorjei/kanibako-oci:latest",
            prep_action="pull", source_ref="oci",
        )
        with patch("kanibako.commands.image.ContainerRuntime") as MockRT, \
                patch("kanibako.commands.image.resolve_rig", return_value=res):
            runtime = MagicMock()
            runtime.guess_containerfile.return_value = None
            runtime.pull.return_value = True
            MockRT.return_value = runtime

            rc = run_prep(self._args(name="oci"))
            assert rc == 0
            runtime.pull.assert_called_once()
            runtime.rebuild.assert_not_called()

    def test_prep_force_rebuilds_already_prepped_template(self, tmp_home, config_file, credentials_dir, capsys):
        """--force does NOT short-circuit an already-prepped template."""
        from kanibako.commands.image import run_prep
        from pathlib import Path

        cf = Path("/x/Containerfile.template-jvm")
        # Already prepped template: prep_action="none", containerfile UNSET.
        res = self._resolution(
            name="jvm", kind="template", image="kanibako-template-jvm",
            prep_action="none",
        )
        config = load_config(config_file)
        std = load_std_paths(config)
        containers_dir = std.data_path / "containers"
        containers_dir.mkdir(parents=True, exist_ok=True)
        (containers_dir / "Containerfile.template-jvm").write_text("FROM x\n")

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT, \
                patch("kanibako.commands.image.resolve_rig", return_value=res), \
                patch("kanibako.commands.image.get_containerfile",
                      return_value=containers_dir / "Containerfile.template-jvm"):
            runtime = MagicMock()
            runtime.rebuild.return_value = 0
            MockRT.return_value = runtime

            rc = run_prep(self._args(name="jvm", force=True))
            assert rc == 0
            # Built despite being already prepped.
            runtime.rebuild.assert_called_once()
        _ = cf  # silence unused

    def test_prep_already_prepped_no_force_short_circuits(self, tmp_home, config_file, credentials_dir, capsys):
        """Already-prepped rig without --force: prints message, no build/pull."""
        from kanibako.commands.image import run_prep

        res = self._resolution(
            name="oci", kind="prefab",
            image="ghcr.io/doctorjei/kanibako-oci:latest", prep_action="none",
        )
        with patch("kanibako.commands.image.ContainerRuntime") as MockRT, \
                patch("kanibako.commands.image.resolve_rig", return_value=res):
            runtime = MagicMock()
            MockRT.return_value = runtime

            rc = run_prep(self._args(name="oci"))
            assert rc == 0
            runtime.rebuild.assert_not_called()
            runtime.pull.assert_not_called()

        assert "already prepped" in capsys.readouterr().out

    def test_prep_all_updates_all(self, tmp_home, config_file, credentials_dir, capsys):
        """--all delegates to _update_all over local images."""
        from kanibako.commands.image import run_prep

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.list_local_images.return_value = [
                ("ghcr.io/foo/kanibako-oci:latest", "1GB"),
                ("ghcr.io/foo/kanibako-lxc:latest", "2GB"),
            ]
            runtime.guess_containerfile.return_value = None
            runtime.pull.return_value = True
            MockRT.return_value = runtime

            rc = run_prep(self._args(all_images=True))
            assert rc == 0
            assert runtime.pull.call_count == 2

    def test_prep_no_name_no_all_errors(self, tmp_home, config_file, credentials_dir, capsys):
        """No name and no --all is an error."""
        from kanibako.commands.image import run_prep

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            MockRT.return_value = MagicMock()
            rc = run_prep(self._args())
            assert rc == 1

        assert "rig name required" in capsys.readouterr().err

    def test_prepare_alias_parses_to_run_prep(self):
        """The 'prepare' alias dispatches to run_prep."""
        from kanibako.cli import build_parser
        from kanibako.commands.image import run_prep

        parser = build_parser()
        ns = parser.parse_args(["rig", "prepare", "jvm"])
        assert ns.func is run_prep
        assert ns.name == "jvm"


class TestRigAdd:
    def _args(self, source, name=None, as_=None, force=False):
        return argparse.Namespace(source=source, name=name, as_=as_, force=force)

    def _std(self, config_file):
        config = load_config(config_file)
        return load_std_paths(config)

    def test_add_image_ref_records_row_no_pull(
        self, tmp_home, config_file, credentials_dir, capsys,
    ):
        """rig add of a registry ref records a prefab row, never pulls."""
        from kanibako.commands.image import run_add
        from kanibako.rig_registry import get, registry_path

        std = self._std(config_file)
        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            MockRT.return_value = runtime

            rc = run_add(self._args("ghcr.io/corp/base:1.0"))
            assert rc == 0
            runtime.pull.assert_not_called()
            runtime.load.assert_not_called()

        rec = get(registry_path(std), "corp/base:1.0")
        assert rec is not None
        assert rec.kind == "prefab"
        assert rec.source_type == "ref"
        assert rec.source == "ghcr.io/corp/base:1.0"
        assert "Added prefab 'corp/base:1.0'" in capsys.readouterr().out

    def test_add_template_installs_containerfile_no_row(
        self, tmp_home, config_file, credentials_dir, capsys, tmp_path,
    ):
        """rig add of a Containerfile installs it under containers/, no row."""
        from kanibako.commands.image import run_add
        from kanibako.containerfiles import get_containerfile
        from kanibako.rig_registry import load_registry, registry_path

        cf = tmp_path / "Containerfile.myjvm"
        cf.write_text("FROM ubuntu\nRUN echo hi\n")

        std = self._std(config_file)
        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            MockRT.return_value = MagicMock()
            rc = run_add(self._args(str(cf)))
            assert rc == 0

        containers_dir = std.data_path / "containers"
        installed = get_containerfile("template-myjvm", containers_dir)
        assert installed is not None
        assert installed.name == "Containerfile.template-myjvm"
        # No registry row for templates.
        assert load_registry(registry_path(std)) == {}
        assert "Added template 'myjvm'" in capsys.readouterr().out

    def test_add_image_tar_loads_and_records(
        self, tmp_home, config_file, credentials_dir, capsys, tmp_path,
    ):
        """rig add of an image tar loads it and records a file-sourced row."""
        from kanibako.commands.image import run_add
        from kanibako.rig_registry import get, registry_path

        tar = tmp_path / "img.tar"
        tar.write_text("fake")
        std = self._std(config_file)

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT, \
                patch("kanibako.commands.image.detect_source_kind", return_value="image"), \
                patch("kanibako.commands.image.derive_name", return_value="foo"):
            runtime = MagicMock()
            # load() returns the actually-loaded image ref (ground truth).
            runtime.load.return_value = "loaded/app:2.0"
            MockRT.return_value = runtime

            rc = run_add(self._args(str(tar), name="foo"))
            assert rc == 0
            runtime.load.assert_called_once()

        rec = get(registry_path(std), "foo")
        assert rec is not None
        assert rec.kind == "prefab"
        assert rec.source_type == "file"
        # The recorded image is what load() reported, not a filename guess.
        assert rec.image == "loaded/app:2.0"
        assert "Added prefab 'foo' from archive." in capsys.readouterr().out

    def test_add_image_tar_untagged_returns_1(
        self, tmp_home, config_file, credentials_dir, capsys, tmp_path,
    ):
        """An archive with no RepoTag (load() -> '') errors and writes no row."""
        from kanibako.commands.image import run_add
        from kanibako.rig_registry import load_registry, registry_path

        tar = tmp_path / "img.tar"
        tar.write_text("fake")
        std = self._std(config_file)

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT, \
                patch("kanibako.commands.image.detect_source_kind", return_value="image"), \
                patch("kanibako.commands.image.derive_name", return_value="foo"):
            runtime = MagicMock()
            runtime.load.return_value = ""
            MockRT.return_value = runtime

            rc = run_add(self._args(str(tar), name="foo"))
            assert rc == 1

        assert load_registry(registry_path(std)) == {}
        assert "no image tag" in capsys.readouterr().err

    def test_add_image_tar_load_failure_returns_1(
        self, tmp_home, config_file, credentials_dir, capsys, tmp_path,
    ):
        """A failed runtime.load surfaces an error and writes no row."""
        from kanibako.commands.image import run_add
        from kanibako.rig_registry import load_registry, registry_path

        tar = tmp_path / "img.tar"
        tar.write_text("fake")
        std = self._std(config_file)

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT, \
                patch("kanibako.commands.image.detect_source_kind", return_value="image"), \
                patch("kanibako.commands.image.derive_name", return_value="foo"):
            runtime = MagicMock()
            runtime.load.return_value = None
            MockRT.return_value = runtime

            rc = run_add(self._args(str(tar), name="foo"))
            assert rc == 1

        assert load_registry(registry_path(std)) == {}
        assert "failed to load image archive" in capsys.readouterr().err

    def test_add_collision_without_force_fails(
        self, tmp_home, config_file, credentials_dir, capsys,
    ):
        """Adding over an existing name without --force returns 1."""
        from kanibako.commands.image import run_add

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            MockRT.return_value = MagicMock()
            assert run_add(self._args("ghcr.io/corp/base:1.0")) == 0
            capsys.readouterr()
            rc = run_add(self._args("ghcr.io/corp/base:1.0"))
            assert rc == 1

        assert "already exists" in capsys.readouterr().err

    def test_add_collision_with_force_overwrites(
        self, tmp_home, config_file, credentials_dir, capsys,
    ):
        """--force overwrites an existing rig row."""
        from kanibako.commands.image import run_add
        from kanibako.rig_registry import get, registry_path

        std = self._std(config_file)
        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            MockRT.return_value = MagicMock()
            assert run_add(self._args("ghcr.io/corp/base:1.0")) == 0
            capsys.readouterr()
            rc = run_add(
                self._args("ghcr.io/corp/base:1.0", name="corp/base:1.0", force=True),
            )
            assert rc == 0

        assert get(registry_path(std), "corp/base:1.0") is not None

    def test_add_underivable_name_fails(
        self, tmp_home, config_file, credentials_dir, capsys, tmp_path,
    ):
        """A bare Containerfile (no derivable name, no --name) returns 1."""
        from kanibako.commands.image import run_add

        cf = tmp_path / "Containerfile"
        cf.write_text("FROM ubuntu\n")
        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            MockRT.return_value = MagicMock()
            rc = run_add(self._args(str(cf)))
            assert rc == 1

        assert "could not derive a rig name" in capsys.readouterr().err

    def test_add_unclassifiable_source_fails(
        self, tmp_home, config_file, credentials_dir, capsys,
    ):
        """A source detect_source_kind can't classify returns 1 with its error."""
        from kanibako.commands.image import run_add

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT, \
                patch(
                    "kanibako.commands.image.detect_source_kind",
                    side_effect=ValueError("cannot classify"),
                ):
            MockRT.return_value = MagicMock()
            rc = run_add(self._args("???"))
            assert rc == 1

        assert "cannot classify" in capsys.readouterr().err

    def test_add_url_fetches_first(
        self, tmp_home, config_file, credentials_dir, capsys, tmp_path,
    ):
        """A URL source is fetched, then classified from the downloaded file."""
        from kanibako.commands.image import run_add
        from kanibako.containerfiles import get_containerfile

        fetched = tmp_path / "Containerfile.fromurl"
        fetched.write_text("FROM ubuntu\n")
        std = self._std(config_file)

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT, \
                patch(
                    "kanibako.commands.image.fetch_to_temp", return_value=fetched,
                ) as mock_fetch:
            MockRT.return_value = MagicMock()
            rc = run_add(self._args("https://example.com/Containerfile.fromurl"))
            assert rc == 0
            mock_fetch.assert_called_once()

        installed = get_containerfile("template-fromurl", std.data_path / "containers")
        assert installed is not None


class TestRigRmUnadd:
    def _std(self, config_file):
        config = load_config(config_file)
        return load_std_paths(config)

    def test_rm_registered_prefab_removes_row(
        self, tmp_home, config_file, credentials_dir, capsys,
    ):
        """rig rm of a registered ref removes the row and returns 0."""
        from kanibako.commands.image import run_add, run_rm
        from kanibako.rig_registry import get, registry_path

        std = self._std(config_file)
        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            MockRT.return_value = MagicMock()
            assert run_add(
                argparse.Namespace(
                    source="ghcr.io/corp/base:1.0", name=None, as_=None, force=False,
                ),
            ) == 0
            capsys.readouterr()

            rc = run_rm(argparse.Namespace(image="corp/base:1.0", force=False))
            assert rc == 0

        assert get(registry_path(std), "corp/base:1.0") is None
        assert "Removed rig 'corp/base:1.0' from the registry." in capsys.readouterr().out

    def test_rm_registered_file_prefab_removes_image(
        self, tmp_home, config_file, credentials_dir, capsys,
    ):
        """A file-sourced prefab also removes its loaded local image."""
        from kanibako.commands.image import run_rm
        from kanibako.rig_registry import RigRecord, registry_path, upsert

        std = self._std(config_file)
        upsert(
            registry_path(std),
            RigRecord(name="foo", kind="prefab", source_type="file", image="foo:latest"),
        )
        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            MockRT.return_value = runtime
            rc = run_rm(argparse.Namespace(image="foo", force=False))
            assert rc == 0
            runtime.remove_image.assert_called_once_with("foo:latest")

    def test_rm_user_template_removes_file(
        self, tmp_home, config_file, credentials_dir, capsys,
    ):
        """rig rm of an installed user template deletes its Containerfile."""
        from kanibako.commands.image import run_rm

        std = self._std(config_file)
        containers_dir = std.data_path / "containers"
        containers_dir.mkdir(parents=True, exist_ok=True)
        cf = containers_dir / "Containerfile.template-mytools"
        cf.write_text("FROM ubuntu\n")

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            MockRT.return_value = MagicMock()
            rc = run_rm(argparse.Namespace(image="mytools", force=False))
            assert rc == 0

        assert not cf.exists()
        assert "Removed user template 'mytools'." in capsys.readouterr().out


class TestExtractGhcrOwner:
    def test_valid_ghcr_url(self):
        from kanibako.commands.image import _extract_ghcr_owner

        assert _extract_ghcr_owner("ghcr.io/doctorjei/kanibako-oci:latest") == "doctorjei"

    def test_non_ghcr_url(self):
        from kanibako.commands.image import _extract_ghcr_owner

        assert _extract_ghcr_owner("docker.io/library/ubuntu:latest") is None


class TestResolveImageName:
    def test_suffix_expansion(self):
        from kanibako.commands.image import resolve_image_name

        result = resolve_image_name("oci", "ghcr.io/doctorjei/kanibako-oci:latest")
        assert result == "ghcr.io/doctorjei/kanibako-oci:latest"

    def test_suffix_min(self):
        from kanibako.commands.image import resolve_image_name

        result = resolve_image_name("min", "ghcr.io/doctorjei/kanibako-oci:latest")
        assert result == "ghcr.io/doctorjei/kanibako-min:latest"

    def test_kanibako_prefix_expansion(self):
        from kanibako.commands.image import resolve_image_name

        result = resolve_image_name("kanibako-custom", "ghcr.io/doctorjei/kanibako-oci:latest")
        assert result == "ghcr.io/doctorjei/kanibako-custom:latest"

    def test_full_path_passthrough(self):
        from kanibako.commands.image import resolve_image_name

        full = "ghcr.io/other/kanibako-oci:v2"
        result = resolve_image_name(full, "ghcr.io/doctorjei/kanibako-oci:latest")
        assert result == full

    def test_unknown_name_passthrough(self):
        from kanibako.commands.image import resolve_image_name

        result = resolve_image_name("ubuntu", "ghcr.io/doctorjei/kanibako-oci:latest")
        assert result == "ubuntu"

    def test_prefix_derived_from_configured(self):
        from kanibako.commands.image import resolve_image_name

        result = resolve_image_name("lxc", "ghcr.io/myowner/kanibako-oci:latest")
        assert result == "ghcr.io/myowner/kanibako-lxc:latest"

    def test_no_prefix_extractable(self):
        from kanibako.commands.image import resolve_image_name

        result = resolve_image_name("oci", "localimage:latest")
        assert result == "oci"


class TestResolveImageReference:
    def _runtime(self, *, has=()):
        """A fake runtime whose image_exists returns True only for *has*."""
        rt = MagicMock()
        rt.image_exists.side_effect = lambda ref: ref in set(has)
        return rt

    def test_qualified_passthrough(self):
        from kanibako.commands.image import resolve_image_reference

        rt = self._runtime()
        full = "ghcr.io/other/kanibako-oci:v2"
        assert resolve_image_reference(full, rt, "ghcr.io/doctorjei/kanibako-oci:latest") == full
        rt.image_exists.assert_not_called()

    def test_non_kanibako_bare_passes_through(self):
        """A public Docker Hub bare name must NOT be rewritten to the kanibako
        registry — the runtime's search registries resolve it."""
        from kanibako.commands.image import resolve_image_reference

        rt = self._runtime()
        assert resolve_image_reference("busybox:latest", rt, "ghcr.io/doctorjei/kanibako-oci:latest") == "busybox:latest"
        assert resolve_image_reference("ubuntu", rt, "ghcr.io/doctorjei/kanibako-oci:latest") == "ubuntu"
        rt.image_exists.assert_not_called()

    def test_kanibako_local_hit_returns_bare(self):
        from kanibako.commands.image import resolve_image_reference

        rt = self._runtime(has=["kanibako-custom:latest"])
        assert resolve_image_reference("kanibako-custom", rt, "ghcr.io/doctorjei/kanibako-oci:latest") == "kanibako-custom:latest"

    def test_kanibako_local_miss_returns_prefixed(self):
        from kanibako.commands.image import resolve_image_reference

        rt = self._runtime()
        result = resolve_image_reference("kanibako-custom", rt, "ghcr.io/doctorjei/kanibako-oci:latest")
        assert result == "ghcr.io/doctorjei/kanibako-custom:latest"

    def test_suffix_expansion_local_miss(self):
        from kanibako.commands.image import resolve_image_reference

        rt = self._runtime()
        result = resolve_image_reference("lxc", rt, "ghcr.io/doctorjei/kanibako-oci:latest")
        assert result == "ghcr.io/doctorjei/kanibako-lxc:latest"

    def test_suffix_expansion_local_hit(self):
        from kanibako.commands.image import resolve_image_reference

        rt = self._runtime(has=["kanibako-oci:latest"])
        result = resolve_image_reference("oci", rt, "ghcr.io/doctorjei/kanibako-oci:latest")
        assert result == "kanibako-oci:latest"

    def test_explicit_tag_preserved(self):
        from kanibako.commands.image import resolve_image_reference

        rt = self._runtime()
        result = resolve_image_reference("kanibako-oci:v2", rt, "ghcr.io/doctorjei/kanibako-oci:latest")
        assert result == "ghcr.io/doctorjei/kanibako-oci:v2"

    def test_prefix_from_custom_registry(self):
        from kanibako.commands.image import resolve_image_reference

        rt = self._runtime()
        result = resolve_image_reference("lxc", rt, "ghcr.io/myowner/kanibako-oci:latest")
        assert result == "ghcr.io/myowner/kanibako-lxc:latest"

    def test_malformed_prefix_falls_back(self):
        from kanibako.commands.image import resolve_image_reference

        rt = self._runtime()
        result = resolve_image_reference("oci", rt, "localimage:latest")
        assert result == "ghcr.io/doctorjei/kanibako-oci:latest"


class TestExtractRegistryPrefix:
    def test_ghcr(self):
        from kanibako.commands.image import _extract_registry_prefix

        assert _extract_registry_prefix("ghcr.io/doctorjei/kanibako-oci:latest") == "ghcr.io/doctorjei"

    def test_two_parts_returns_none(self):
        from kanibako.commands.image import _extract_registry_prefix

        assert _extract_registry_prefix("library/ubuntu:latest") is None

    def test_single_part_returns_none(self):
        from kanibako.commands.image import _extract_registry_prefix

        assert _extract_registry_prefix("ubuntu:latest") is None


class TestRebuildWithShorthand:
    def test_shorthand_resolved_in_rebuild(self, tmp_home, config_file, credentials_dir, capsys):
        """Shorthand name gets resolved to full image in rebuild."""
        from kanibako.commands.image import run_rebuild

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.pull.return_value = True
            runtime.guess_containerfile.return_value = None
            MockRT.return_value = runtime

            args = argparse.Namespace(
                image="min",
                all_images=False,
            )
            rc = run_rebuild(args)
            assert rc == 0
            # Should have resolved "min" to the full image path
            call_args = runtime.pull.call_args[0]
            assert "kanibako-min" in call_args[0]
            assert call_args[0].startswith("ghcr.io/")


# ---------------------------------------------------------------------------
# _list_remote_packages
# ---------------------------------------------------------------------------

class TestListRemotePackages:
    def test_successful_api_response(self, capsys):
        response_data = [
            {"name": "kanibako-oci"},
            {"name": "kanibako-lxc"},
            {"name": "unrelated-pkg"},
        ]
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(response_data).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("kanibako.commands.image.urllib.request.urlopen", return_value=mock_resp):
            _list_remote_packages("myowner")

        out = capsys.readouterr().out
        assert "ghcr.io/myowner/kanibako-oci" in out
        assert "ghcr.io/myowner/kanibako-lxc" in out
        assert "unrelated-pkg" not in out

    def test_api_timeout(self, capsys):
        import urllib.error
        with patch(
            "kanibako.commands.image.urllib.request.urlopen",
            side_effect=urllib.error.URLError("timeout"),
        ):
            _list_remote_packages("owner")

        out = capsys.readouterr().out
        assert "could not reach" in out.lower()

    def test_empty_package_list(self, capsys):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps([]).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("kanibako.commands.image.urllib.request.urlopen", return_value=mock_resp):
            _list_remote_packages("owner")

        out = capsys.readouterr().out
        assert "no kanibako packages" in out.lower()

    def test_invalid_json_response(self, capsys):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("kanibako.commands.image.urllib.request.urlopen", return_value=mock_resp):
            _list_remote_packages("owner")

        out = capsys.readouterr().out
        assert "could not reach" in out.lower()


# ---------------------------------------------------------------------------
# _extract_ghcr_owner edge cases
# ---------------------------------------------------------------------------

class TestExtractGhcrOwnerExtended:
    def test_non_ghcr_image_returns_none(self):
        assert _extract_ghcr_owner("docker.io/library/ubuntu:latest") is None

    def test_ghcr_no_slash_after_owner(self):
        """ghcr.io/owner without a slash after owner returns None."""
        assert _extract_ghcr_owner("ghcr.io/justowner") is None

    def test_ghcr_with_nested_path(self):
        assert _extract_ghcr_owner("ghcr.io/org/repo/image:tag") == "org"


# ---------------------------------------------------------------------------
# Template create / list / delete (absorbed from template_cmd)
# ---------------------------------------------------------------------------

class TestImageCreate:
    """Interactive ``rig create`` is now a deprecated alias for ``rig extend``.

    It commits to ``kanibako-rig-<name>`` (extended), writes a registry row, and
    emits a deprecation notice. These tests pin the delegated behavior; the
    foundation (``--base``) is treated as already prepped via
    ``image_exists.return_value = True``.
    """

    def _make_args(self, name="jvm", base="kanibako-oci",
                   always_commit=False, no_commit_on_error=False):
        return argparse.Namespace(
            name=name, base=base, template=None,
            always_commit=always_commit, no_commit_on_error=no_commit_on_error,
        )

    def _runtime(self):
        runtime = MagicMock()
        # Only the resolved prefab ref (kanibako-<base>:latest) exists, so the
        # foundation resolves to prep_action "none"; the template/extended local
        # probes (no ':latest') stay False.
        runtime.image_exists.side_effect = lambda img: img.endswith(":latest")
        runtime.list_local_images.return_value = []
        runtime.run_interactive.return_value = 0
        runtime.cp.return_value = True
        return runtime

    def test_create_runs_container_and_commits(
        self, tmp_home, config_file, credentials_dir, capsys,
    ):
        from kanibako.commands.image import run_create

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = self._runtime()
            MockRT.return_value = runtime

            rc = run_create(self._make_args())
            assert rc == 0

            runtime.run_interactive.assert_called_once_with(
                "kanibako-oci:latest",
                container_name="kanibako-extend-jvm",
            )
            runtime.commit.assert_called_once_with(
                "kanibako-extend-jvm",
                "kanibako-rig-jvm",
            )
            # Build container should be cleaned up
            runtime.rm.assert_called_once_with("kanibako-extend-jvm")

        assert "deprecated" in capsys.readouterr().err

    def test_create_always_commit_on_nonzero_exit(
        self, tmp_home, config_file, credentials_dir, capsys,
    ):
        from kanibako.commands.image import run_create

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = self._runtime()
            runtime.run_interactive.return_value = 1
            MockRT.return_value = runtime

            rc = run_create(self._make_args(name="tools", base="kanibako-min",
                                            always_commit=True))
            assert rc == 0
            runtime.commit.assert_called_once()

    def test_create_no_commit_on_error(
        self, tmp_home, config_file, credentials_dir, capsys,
    ):
        from kanibako.commands.image import run_create

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = self._runtime()
            runtime.run_interactive.return_value = 1
            MockRT.return_value = runtime

            rc = run_create(self._make_args(no_commit_on_error=True))
            assert rc == 1
            runtime.commit.assert_not_called()

        captured = capsys.readouterr()
        assert "Skipping commit" in captured.err

    def test_create_prompt_confirm_yes(
        self, tmp_home, config_file, credentials_dir, capsys, monkeypatch,
    ):
        """Default behavior: prompt on error, user says yes."""
        from kanibako.commands.image import run_create

        monkeypatch.setattr("builtins.input", lambda _: "y")

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = self._runtime()
            runtime.run_interactive.return_value = 1
            MockRT.return_value = runtime

            rc = run_create(self._make_args())
            assert rc == 0
            runtime.commit.assert_called_once()

    def test_create_prompt_confirm_no(
        self, tmp_home, config_file, credentials_dir, capsys, monkeypatch,
    ):
        """Default behavior: prompt on error, user says no."""
        from kanibako.commands.image import run_create

        monkeypatch.setattr("builtins.input", lambda _: "n")

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = self._runtime()
            runtime.run_interactive.return_value = 1
            MockRT.return_value = runtime

            rc = run_create(self._make_args())
            assert rc == 1
            runtime.commit.assert_not_called()

    def test_create_no_prompt_on_zero_exit(
        self, tmp_home, config_file, credentials_dir, capsys, monkeypatch,
    ):
        """No prompt when container exits cleanly."""
        from kanibako.commands.image import run_create

        # input() should never be called
        monkeypatch.setattr("builtins.input", lambda _: (_ for _ in ()).throw(AssertionError("should not prompt")))

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = self._runtime()
            MockRT.return_value = runtime

            rc = run_create(self._make_args())
            assert rc == 0
            runtime.commit.assert_called_once()

    def test_create_rejects_invalid_name(
        self, tmp_home, config_file, credentials_dir, capsys,
    ):
        from kanibako.commands.image import run_create

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = self._runtime()
            MockRT.return_value = runtime

            rc = run_create(self._make_args(name="../evil"))
            assert rc == 1
            runtime.run_interactive.assert_not_called()

        captured = capsys.readouterr()
        assert "Invalid template name" in captured.err

    def test_create_fails_on_commit_error(
        self, tmp_home, config_file, credentials_dir, capsys,
    ):
        from kanibako.commands.image import run_create
        from kanibako.errors import ContainerError

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = self._runtime()
            runtime.commit.side_effect = ContainerError("commit failed")
            MockRT.return_value = runtime

            rc = run_create(self._make_args(name="bad"))
            assert rc == 1


class TestImageCreateTemplate:
    def _make_args(self, name="my-jvm", base=None, template="jvm"):
        return argparse.Namespace(
            name=name, base=base, template=template,
            always_commit=False, no_commit_on_error=False,
        )

    def test_template_builds_containerfile(self, tmp_home, config_file, credentials_dir, capsys):
        """--template builds the bundled Containerfile.template-<name> instead
        of running an interactive session + commit. With no --base, the
        template's declared base default stands (build_args=None)."""
        from kanibako.commands.image import run_create

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.rebuild.return_value = 0
            MockRT.return_value = runtime

            rc = run_create(self._make_args())
            assert rc == 0

            # No interactive run / no commit on the build path.
            runtime.run_interactive.assert_not_called()
            runtime.commit.assert_not_called()

            runtime.rebuild.assert_called_once()
            call_args, call_kwargs = runtime.rebuild.call_args
            # Output tag is kanibako-template-<positional-name>.
            assert call_args[0] == "kanibako-template-my-jvm"
            # Containerfile path points at the jvm template.
            containerfile = call_args[1]
            assert str(containerfile).endswith("Containerfile.template-jvm")
            # No --base -> no BASE_IMAGE override; the Containerfile default stands.
            assert call_kwargs["build_args"] is None

        captured = capsys.readouterr()
        assert "Template saved as kanibako-template-my-jvm" in captured.out
        assert "from its default base" in captured.out
        # Deprecation notice emitted on stderr, behavior otherwise unchanged.
        assert "note: 'rig create --template' is deprecated" in captured.err

    def test_template_base_override(self, tmp_home, config_file, credentials_dir, capsys):
        """An explicit --base overrides the template's declared base and prints
        an override note."""
        from kanibako.commands.image import run_create, resolve_image_name

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.rebuild.return_value = 0
            MockRT.return_value = runtime

            rc = run_create(self._make_args(base="kanibako-lxc"))
            assert rc == 0

            runtime.rebuild.assert_called_once()
            _call_args, call_kwargs = runtime.rebuild.call_args
            build_args = call_kwargs["build_args"]
            assert build_args is not None
            expected = resolve_image_name(
                "kanibako-lxc", "ghcr.io/doctorjei/kanibako-oci:latest",
            )
            assert build_args["BASE_IMAGE"] == expected
            assert expected == "ghcr.io/doctorjei/kanibako-lxc:latest"

        captured = capsys.readouterr()
        # Override note printed when --base is supplied.
        assert "Note: overriding template 'jvm' default base" in captured.out
        assert expected in captured.out

    def test_unknown_template_lists_available(self, tmp_home, config_file, credentials_dir, capsys):
        """An unknown --template returns non-zero, lists available templates,
        and never invokes a build."""
        from kanibako.commands.image import run_create

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            MockRT.return_value = runtime

            rc = run_create(self._make_args(template="bogus"))
            assert rc == 1
            runtime.rebuild.assert_not_called()
            runtime.run_interactive.assert_not_called()

        captured = capsys.readouterr()
        assert "unknown template 'bogus'" in captured.err
        assert "jvm" in captured.err

    def test_no_template_delegates_to_extend(self, tmp_home, config_file, credentials_dir, capsys):
        """Without --template, interactive create delegates to 'rig extend':
        commits to kanibako-rig-<name> with a registry row, not the old
        kanibako-template-<name> template image."""
        from kanibako.commands.image import run_create

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.image_exists.side_effect = lambda img: img.endswith(":latest")
            runtime.list_local_images.return_value = []
            runtime.run_interactive.return_value = 0
            runtime.cp.return_value = True
            MockRT.return_value = runtime

            args = argparse.Namespace(
                name="my-box", base="kanibako-oci", template=None,
                always_commit=False, no_commit_on_error=False,
            )
            rc = run_create(args)
            assert rc == 0

            runtime.run_interactive.assert_called_once_with(
                "kanibako-oci:latest",
                container_name="kanibako-extend-my-box",
            )
            runtime.commit.assert_called_once_with(
                "kanibako-extend-my-box",
                "kanibako-rig-my-box",
            )
            runtime.rebuild.assert_not_called()

        assert "deprecated" in capsys.readouterr().err


class TestRigExtend:
    """``rig extend NAME --from FOUNDATION``: auto-prep foundation, interactive
    session, write in-image rig.yaml, commit as kanibako-rig-<name>, record a row.
    """

    def _args(self, name="mydev", from_="jvm",
              always_commit=False, no_commit_on_error=False):
        return argparse.Namespace(
            name=name, from_=from_,
            always_commit=always_commit, no_commit_on_error=no_commit_on_error,
        )

    def _std(self, config_file):
        config = load_config(config_file)
        return load_std_paths(config)

    def _runtime(self):
        runtime = MagicMock()
        runtime.image_exists.return_value = False
        runtime.list_local_images.return_value = []
        runtime.run_interactive.return_value = 0
        runtime.rebuild.return_value = 0
        runtime.cp.return_value = True
        return runtime

    def test_extend_from_template_autobuilds_then_commits(
        self, tmp_home, config_file, credentials_dir, capsys,
    ):
        """Unprepped template foundation -> auto-build, then interactive+commit;
        a registry row is written for the new extended rig."""
        from kanibako.commands.image import run_extend
        from kanibako.rig_registry import load_registry, registry_path

        std = self._std(config_file)
        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = self._runtime()
            MockRT.return_value = runtime

            rc = run_extend(self._args(name="mydev", from_="jvm"))
            assert rc == 0

            # Auto-prep built the template foundation.
            runtime.rebuild.assert_called_once()
            runtime.run_interactive.assert_called_once()
            _ri_args, ri_kwargs = runtime.run_interactive.call_args
            assert ri_kwargs["container_name"] == "kanibako-extend-mydev"
            runtime.commit.assert_called_once_with(
                "kanibako-extend-mydev", "kanibako-rig-mydev",
            )
            # cp targets /etc/ (so rig.yaml lands at /etc/kanibako/rig.yaml).
            cp_args, _cp_kwargs = runtime.cp.call_args
            assert cp_args[1] == "kanibako-extend-mydev:/etc/"

            # Call order: run_interactive -> cp -> commit.
            relevant = [
                name for name, _a, _k in runtime.method_calls
                if name in ("run_interactive", "cp", "commit")
            ]
            assert relevant == ["run_interactive", "cp", "commit"]

        reg = load_registry(registry_path(std))
        assert "mydev" in reg
        assert reg["mydev"].kind == "extended"
        assert reg["mydev"].image == "kanibako-rig-mydev"
        assert reg["mydev"].parent

    def test_extend_from_prepped_foundation_no_autoprep(
        self, tmp_home, config_file, credentials_dir, capsys,
    ):
        """An already-prepped foundation triggers no build/pull."""
        from kanibako.commands.image import run_extend

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = self._runtime()
            # Only the foundation's resolved image exists -> prep_action "none".
            runtime.image_exists.side_effect = (
                lambda img: img == "kanibako-oci:latest"
            )
            MockRT.return_value = runtime

            rc = run_extend(self._args(name="mydev", from_="oci"))
            assert rc == 0
            runtime.rebuild.assert_not_called()
            runtime.pull.assert_not_called()
            runtime.run_interactive.assert_called_once()
            runtime.commit.assert_called_once()

    def test_extend_missing_extended_foundation_errors(
        self, tmp_home, config_file, credentials_dir, capsys,
    ):
        """A registered-but-missing extended foundation can't be re-prepped."""
        from kanibako.commands.image import run_extend
        from kanibako.rig_registry import RigRecord, registry_path, upsert

        std = self._std(config_file)
        upsert(
            registry_path(std),
            RigRecord(name="ghost", kind="extended", image="kanibako-rig-ghost"),
        )

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = self._runtime()
            runtime.image_exists.return_value = False  # image absent
            MockRT.return_value = runtime

            rc = run_extend(self._args(name="mydev", from_="ghost"))
            assert rc == 1
            runtime.run_interactive.assert_not_called()

        assert "missing" in capsys.readouterr().err

    def test_extend_no_commit_on_error(
        self, tmp_home, config_file, credentials_dir, capsys,
    ):
        """Non-zero container exit + --no-commit-on-error -> rc 1, no commit."""
        from kanibako.commands.image import run_extend

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = self._runtime()
            runtime.run_interactive.return_value = 1
            MockRT.return_value = runtime

            rc = run_extend(self._args(no_commit_on_error=True))
            assert rc == 1
            runtime.commit.assert_not_called()
            runtime.rm.assert_called_once_with("kanibako-extend-mydev")

    def test_extend_always_commit_on_error(
        self, tmp_home, config_file, credentials_dir, capsys,
    ):
        """Non-zero container exit + --always-commit -> commits anyway."""
        from kanibako.commands.image import run_extend

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = self._runtime()
            runtime.run_interactive.return_value = 1
            MockRT.return_value = runtime

            rc = run_extend(self._args(always_commit=True))
            assert rc == 0
            runtime.commit.assert_called_once()

    def test_extend_cp_failure_aborts_commit(
        self, tmp_home, config_file, credentials_dir, capsys,
    ):
        """A failed metadata cp -> rc 1, no commit, container still cleaned up."""
        from kanibako.commands.image import run_extend

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = self._runtime()
            runtime.cp.return_value = False
            MockRT.return_value = runtime

            rc = run_extend(self._args())
            assert rc == 1
            runtime.commit.assert_not_called()
            runtime.rm.assert_called_once_with("kanibako-extend-mydev")

        assert "failed to write rig metadata" in capsys.readouterr().err


class TestImageCreateFlags:
    def test_create_flags_are_mutually_exclusive(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["rig", "create", "jvm", "--always-commit", "--no-commit-on-error"])

    def test_rig_extend_parser_exists(self):
        from kanibako.cli import build_parser
        from kanibako.commands.image import run_extend
        parser = build_parser()
        args = parser.parse_args(["rig", "extend", "mydev", "--from", "jvm"])
        assert args.func is run_extend
        assert args.name == "mydev"
        assert args.from_ == "jvm"


class TestImageRegistration:
    def test_template_not_in_subcommands(self):
        from kanibako.cli import _SUBCOMMANDS
        assert "template" not in _SUBCOMMANDS

    def test_rig_in_subcommands(self):
        from kanibako.cli import _SUBCOMMANDS
        assert "rig" in _SUBCOMMANDS

    def test_image_alias_in_subcommands(self):
        from kanibako.cli import _SUBCOMMANDS
        assert "image" in _SUBCOMMANDS

    def test_rig_create_parser_exists(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["rig", "create", "jvm"])
        assert args.command == "rig"
        assert args.rig_command == "create"
        assert args.name == "jvm"

    def test_rig_info_parser_exists(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["rig", "info", "kanibako-oci"])
        assert args.command == "rig"
        assert args.image == "kanibako-oci"

    def test_rig_inspect_alias(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["rig", "inspect", "kanibako-oci"])
        assert args.command == "rig"
        assert args.image == "kanibako-oci"

    def test_rig_rm_parser_exists(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["rig", "rm", "kanibako-oci"])
        assert args.command == "rig"
        assert args.image == "kanibako-oci"

    def test_rig_delete_alias(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["rig", "delete", "kanibako-oci"])
        assert args.command == "rig"
        assert args.image == "kanibako-oci"

    def test_rig_list_quiet_flag(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["rig", "list", "-q"])
        assert args.command == "rig"
        assert args.quiet is True

    def test_rig_rebuild_no_local_flag(self):
        """--local flag should no longer exist on rebuild."""
        from kanibako.cli import build_parser
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["rig", "rebuild", "--local"])

    def test_rig_list_no_project_flag(self):
        """-p/--project flag should no longer exist on list."""
        from kanibako.cli import build_parser
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["rig", "list", "-p", "foo"])


# ---------------------------------------------------------------------------
# ContainerRuntime.image_inspect
# ---------------------------------------------------------------------------

class TestContainerRuntimeImageInspect:
    def test_image_inspect_returns_dict(self):
        from kanibako.container import ContainerRuntime

        with patch("kanibako.container.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps([{"Id": "sha256:abc", "Size": 100}]),
            )
            rt = ContainerRuntime(command="podman")
            result = rt.image_inspect("test-image")
            assert result == {"Id": "sha256:abc", "Size": 100}

    def test_image_inspect_not_found(self):
        from kanibako.container import ContainerRuntime

        with patch("kanibako.container.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="not found")
            rt = ContainerRuntime(command="podman")
            result = rt.image_inspect("nosuch")
            assert result is None

    def test_image_inspect_dict_response(self):
        """Docker returns a dict, not a list."""
        from kanibako.container import ContainerRuntime

        with patch("kanibako.container.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({"Id": "sha256:def", "Size": 200}),
            )
            rt = ContainerRuntime(command="docker")
            result = rt.image_inspect("test-image")
            assert result == {"Id": "sha256:def", "Size": 200}

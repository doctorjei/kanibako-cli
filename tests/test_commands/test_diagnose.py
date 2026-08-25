"""Tests for kanibako.commands.diagnose."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kanibako.commands.diagnose import (
    _check_agents,
    _check_image,
    _check_journal,
    _check_runtime,
    _check_storage,
    _check_vscode,
    _diagnose_baseline,
    _format_check,
    probe_missing_executables,
    run_box_diagnose,
    run_rig_diagnose,
    run_system_diagnose,
)


class TestFormatCheck:
    def test_format_ok(self) -> None:
        result = _format_check("ok", "Runtime", "podman 5.0")
        assert result == "[ok] Runtime: podman 5.0"

    def test_format_error(self) -> None:
        result = _format_check("!!", "Runtime", "not found")
        assert result == "[!!] Runtime: not found"

    def test_format_skip(self) -> None:
        result = _format_check("--", "Storage", "cannot check")
        assert result == "[--] Storage: cannot check"


class TestCheckRuntime:
    def test_check_runtime_no_runtime(self) -> None:
        """When ContainerRuntime raises, returns error status."""
        from kanibako.errors import ContainerError

        with patch(
            "kanibako.runtime.container.ContainerRuntime",
            side_effect=ContainerError("no runtime"),
        ):
            status, detail = _check_runtime()
        assert status == "!!"
        assert "not found" in detail

    def test_check_runtime_found(self) -> None:
        """When ContainerRuntime succeeds, returns ok status."""
        mock_runtime = MagicMock()
        mock_runtime.cmd = "podman"
        with (
            patch(
                "kanibako.runtime.container.ContainerRuntime",
                return_value=mock_runtime,
            ),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                returncode=0, stdout="podman version 5.0.0"
            )
            status, detail = _check_runtime()
        assert status == "ok"
        assert "podman" in detail
        assert "5.0.0" in detail


class TestCheckAgents:
    def test_check_agents_none(self) -> None:
        """No agent plugins returns error status."""
        with patch(
            "kanibako.targets.discover_targets", return_value={}
        ):
            results = _check_agents()
        assert len(results) == 1
        assert results[0][0] == "!!"
        assert results[0][1] == "Agents"
        assert "no agent plugins" in results[0][2]

    def test_check_agents_found(self, tmp_path: Path) -> None:
        """Discovered agent with detect() returning install (existing binary) -> ok."""
        binary = tmp_path / "claude"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)

        mock_install = MagicMock()
        mock_install.binary = binary

        mock_target = MagicMock()
        mock_target.display_name = "Claude Code"
        mock_target.has_binary = True
        mock_target.detect.return_value = mock_install

        mock_cls = MagicMock(return_value=mock_target)
        with patch(
            "kanibako.targets.discover_targets",
            return_value={"claude": mock_cls},
        ):
            results = _check_agents()
        assert len(results) == 1
        assert results[0][0] == "ok"
        assert "Claude Code" in results[0][1]
        assert str(binary) in results[0][2]

    def test_check_agents_not_installed_is_optional(self) -> None:
        """A real agent (has_binary True) that isn't installed -> [--] optional.

        A not-installed optional agent is informational, NOT an error.
        """
        mock_target = MagicMock()
        mock_target.display_name = "Claude Code"
        mock_target.has_binary = True
        mock_target.detect.return_value = None

        mock_cls = MagicMock(return_value=mock_target)
        with patch(
            "kanibako.targets.discover_targets",
            return_value={"claude": mock_cls},
        ):
            results = _check_agents()
        assert len(results) == 1
        assert results[0][0] == "--"
        assert "not installed (optional)" in results[0][2]

    def test_detected_agent_existing_binary_ok(self, tmp_path: Path) -> None:
        """A detected agent whose binary exists on disk -> [ok] with the path."""
        binary = tmp_path / "claude"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)

        mock_install = MagicMock()
        mock_install.binary = binary

        mock_target = MagicMock()
        mock_target.display_name = "Claude Code"
        mock_target.has_binary = True
        mock_target.detect.return_value = mock_install

        mock_cls = MagicMock(return_value=mock_target)
        with patch(
            "kanibako.targets.discover_targets",
            return_value={"claude": mock_cls},
        ):
            results = _check_agents()
        assert len(results) == 1
        assert results[0][0] == "ok"
        assert str(binary) in results[0][2]
        assert "not found" not in results[0][2]

    def test_detected_agent_missing_binary_errors(self) -> None:
        """A detected agent whose recorded binary is dangling -> [!!]."""
        mock_install = MagicMock()
        mock_install.binary = Path("/nonexistent/x")

        mock_target = MagicMock()
        mock_target.display_name = "Claude Code"
        mock_target.has_binary = True
        mock_target.detect.return_value = mock_install

        mock_cls = MagicMock(return_value=mock_target)
        with patch(
            "kanibako.targets.discover_targets",
            return_value={"claude": mock_cls},
        ):
            results = _check_agents()
        assert len(results) == 1
        assert results[0][0] == "!!"
        assert "binary not found at" in results[0][2]
        assert "/nonexistent/x" in results[0][2]

    def test_detected_agent_empty_binary_errors(self, tmp_path: Path) -> None:
        """A detected agent whose binary exists but is 0 bytes -> [!!].

        A 0-byte file passes a bare exists() check yet bricks the box at
        launch; diagnose must flag it.
        """
        binary = tmp_path / "claude"
        binary.touch()  # 0 bytes
        binary.chmod(0o755)

        mock_install = MagicMock()
        mock_install.binary = binary

        mock_target = MagicMock()
        mock_target.display_name = "Claude Code"
        mock_target.has_binary = True
        mock_target.detect.return_value = mock_install

        mock_cls = MagicMock(return_value=mock_target)
        with patch(
            "kanibako.targets.discover_targets",
            return_value={"claude": mock_cls},
        ):
            results = _check_agents()
        assert len(results) == 1
        assert results[0][0] == "!!"
        assert "0 bytes" in results[0][2]
        assert str(binary) in results[0][2]

    def test_detected_agent_nonexecutable_binary_errors(
        self, tmp_path: Path
    ) -> None:
        """A detected agent whose binary exists but lacks the exec bit -> [!!]."""
        binary = tmp_path / "claude"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o644)  # not executable

        mock_install = MagicMock()
        mock_install.binary = binary

        mock_target = MagicMock()
        mock_target.display_name = "Claude Code"
        mock_target.has_binary = True
        mock_target.detect.return_value = mock_install

        mock_cls = MagicMock(return_value=mock_target)
        with patch(
            "kanibako.targets.discover_targets",
            return_value={"claude": mock_cls},
        ):
            results = _check_agents()
        assert len(results) == 1
        assert results[0][0] == "!!"
        assert "not executable" in results[0][2]
        assert str(binary) in results[0][2]

    def test_no_agent_fallback_shows_resolved_shell(self) -> None:
        """The no-binary Shell fallback is OK and shows the resolved box.shell.

        It needs no host binary and is always available, so diagnose must NOT
        report it as a missing agent; instead it shows the resolved launch shell
        and its source.
        """
        mock_target = MagicMock()
        mock_target.display_name = "Shell"
        mock_target.has_binary = False

        mock_cls = MagicMock(return_value=mock_target)
        with (
            patch(
                "kanibako.targets.discover_targets",
                return_value={"no_agent": mock_cls},
            ),
            patch(
                "kanibako.launch.shells.resolve_box_shell",
                return_value=("/bin/bash", "image"),
            ),
        ):
            results = _check_agents(config=MagicMock(), std=MagicMock())
        assert len(results) == 1
        status, label, detail = results[0]
        assert status == "ok"
        assert "Shell" in label
        assert "/bin/bash" in detail
        assert "image default" in detail
        assert "not found" not in detail

    def test_no_agent_fallback_source_labels(self) -> None:
        """Each resolver source token maps to the right friendly label."""
        cases = {
            "box.shell": ("/bin/zsh", "box.shell"),
            "$KANIBAKO_SHELL": ("/usr/bin/fish", "$KANIBAKO_SHELL"),
            "image": ("/bin/bash", "image default"),
            "sh": ("sh", "fallback"),
        }
        for source, (shell, label) in cases.items():
            mock_target = MagicMock()
            mock_target.display_name = "Shell"
            mock_target.has_binary = False
            mock_cls = MagicMock(return_value=mock_target)
            with (
                patch(
                    "kanibako.targets.discover_targets",
                    return_value={"no_agent": mock_cls},
                ),
                patch(
                    "kanibako.launch.shells.resolve_box_shell",
                    return_value=(shell, source),
                ),
            ):
                results = _check_agents(config=MagicMock(), std=MagicMock())
            detail = results[0][2]
            assert detail == f"{shell} ({label})", (source, detail)

    def test_no_agent_fallback_without_config_is_safe(self) -> None:
        """Without config/std the Shell line falls back to sh, never crashing."""
        mock_target = MagicMock()
        mock_target.display_name = "Shell"
        mock_target.has_binary = False
        mock_cls = MagicMock(return_value=mock_target)
        with patch(
            "kanibako.targets.discover_targets",
            return_value={"no_agent": mock_cls},
        ):
            results = _check_agents()
        assert len(results) == 1
        assert results[0][0] == "ok"
        assert results[0][2] == "sh (fallback)"


class TestCheckStorage:
    def test_check_storage(self, tmp_path: Path) -> None:
        """Test with a real temporary path."""
        status, detail = _check_storage(tmp_path)
        assert status in ("ok", "!!")
        assert "GB" in detail
        assert str(tmp_path) in detail

    def test_check_storage_nonexistent(self) -> None:
        """Non-existent path returns skip status."""
        status, detail = _check_storage(Path("/nonexistent/path/xyz"))
        assert status == "--"
        assert "cannot check" in detail


class TestCheckJournal:
    """J2: diagnose surfaces lingering lifecycle-journal entries."""

    def _std(self, tmp_path: Path):
        from types import SimpleNamespace
        return SimpleNamespace(journal=tmp_path / "journal.yaml")

    def test_empty_journal_is_clean_ok(self, tmp_path: Path) -> None:
        """A normally-empty journal reports a single clean ok line."""
        lines = _check_journal(self._std(tmp_path))
        assert lines == [("ok", "no in-flight operations")]

    def test_pending_entry_reported_as_warning(self, tmp_path: Path) -> None:
        """A pending entry → a `!!` finding carrying op + box + started_at."""
        from kanibako.launch import journal

        std = self._std(tmp_path)
        box = tmp_path / "boxes" / "myapp"
        journal.write_entry(std.journal, box, op="import", name="myapp", mode="primary")

        lines = _check_journal(std)
        assert len(lines) == 1
        status, detail = lines[0]
        assert status == "!!"
        assert "import" in detail
        assert "myapp" in detail
        assert str(box) in detail

    def test_box_key_filters_to_that_box(self, tmp_path: Path) -> None:
        """With box_key, only THAT box's entry is surfaced (a clean ok for an
        unrelated box even when other entries exist)."""
        from kanibako.launch import journal

        std = self._std(tmp_path)
        mine = tmp_path / "boxes" / "mine"
        other = tmp_path / "boxes" / "other"
        journal.write_entry(std.journal, other, op="import", name="other", mode="primary")

        # My box has no entry → clean even though the journal is non-empty.
        assert _check_journal(std, box_key=str(mine)) == [
            ("ok", "no in-flight operations")
        ]
        # My box's own entry IS surfaced when present.
        journal.write_entry(std.journal, mine, op="connect", name="mine", mode="named")
        lines = _check_journal(std, box_key=str(mine))
        assert len(lines) == 1
        assert lines[0][0] == "!!"
        assert "mine" in lines[0][1]

    def test_multiple_entries_each_reported(self, tmp_path: Path) -> None:
        from kanibako.launch import journal

        std = self._std(tmp_path)
        journal.write_entry(std.journal, tmp_path / "a", op="import", name="a", mode="primary")
        journal.write_entry(std.journal, tmp_path / "b", op="connect", name="b", mode="named")
        lines = _check_journal(std)
        assert len(lines) == 2
        assert all(s == "!!" for s, _ in lines)

    def test_unreadable_journal_degrades_to_skip(self) -> None:
        # A std whose .journal access blows up → defensive `--` line.
        class Boom:
            @property
            def journal(self):
                raise RuntimeError("boom")

        lines = _check_journal(Boom())
        assert lines == [("--", "cannot read journal")]


class TestSystemDiagnoseJournal:
    def test_system_diagnose_reports_pending_entry(
        self, config_file, tmp_home, credentials_dir, capsys,
    ) -> None:
        """A pending journal entry shows up in `run_system_diagnose` output."""
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths
        from kanibako.launch import journal
        from kanibako.errors import ContainerError

        config = load_config(config_file)
        std = load_std_paths(config)
        journal.write_entry(
            std.journal, tmp_home / "boxes" / "stuck",
            op="import", name="stuck", mode="primary",
        )
        with patch(
            "kanibako.runtime.container.ContainerRuntime",
            side_effect=ContainerError("none"),
        ):
            rc = run_system_diagnose(argparse.Namespace())
        out = capsys.readouterr().out
        assert rc == 0
        assert "Journal" in out
        assert "stuck" in out

    def test_system_diagnose_clean_when_empty(
        self, config_file, tmp_home, credentials_dir, capsys,
    ) -> None:
        from kanibako.errors import ContainerError

        with patch(
            "kanibako.runtime.container.ContainerRuntime",
            side_effect=ContainerError("none"),
        ):
            rc = run_system_diagnose(argparse.Namespace())
        out = capsys.readouterr().out
        assert rc == 0
        assert "[ok] Journal: no in-flight operations" in out


class TestCheckImage:
    def test_check_image_found(self) -> None:
        """Image available locally returns ok."""
        mock_config = MagicMock()
        mock_config.box_image = "kanibako-oci:latest"

        mock_runtime = MagicMock()
        mock_runtime.image_inspect.return_value = {"Id": "abc123"}

        with patch(
            "kanibako.runtime.container.ContainerRuntime",
            return_value=mock_runtime,
        ):
            status, detail = _check_image(mock_config)
        assert status == "ok"
        assert "available locally" in detail

    def test_check_image_not_found(self) -> None:
        """Image not locally available returns error."""
        mock_config = MagicMock()
        mock_config.box_image = "kanibako-oci:latest"

        mock_runtime = MagicMock()
        mock_runtime.image_inspect.return_value = None

        with patch(
            "kanibako.runtime.container.ContainerRuntime",
            return_value=mock_runtime,
        ):
            status, detail = _check_image(mock_config)
        assert status == "!!"
        assert "not found locally" in detail


class TestRunSystemDiagnose:
    def test_run_system_diagnose(
        self, config_file, tmp_home, credentials_dir, capsys
    ) -> None:
        """System diagnose runs and returns 0."""
        from kanibako.errors import ContainerError

        with patch(
            "kanibako.runtime.container.ContainerRuntime",
            side_effect=ContainerError("none"),
        ):
            args = argparse.Namespace()
            rc = run_system_diagnose(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Kanibako System Diagnostics" in captured.out
        assert "[" in captured.out


class TestRunRigDiagnose:
    def test_run_rig_diagnose(
        self, config_file, tmp_home, credentials_dir, capsys
    ) -> None:
        """Rig diagnose runs and returns 0."""
        from kanibako.errors import ContainerError

        with patch(
            "kanibako.runtime.container.ContainerRuntime",
            side_effect=ContainerError("none"),
        ):
            args = argparse.Namespace()
            rc = run_rig_diagnose(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Rig (Image) Diagnostics" in captured.out


class TestRunBoxDiagnose:
    """run_box_diagnose: only report internals for a real, registered project.

    `resolve_any_project` fabricates a default-mode resolution for ANY
    existing directory, so diagnose must verify a project is actually
    registered (persisted box.yaml) before reporting on its shell/etc.
    """

    def _register_default_project(self, config_file, tmp_home, credentials_dir):
        """Initialize a registered default project at the cwd; return its paths."""
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        with patch(
            "kanibako.runtime.container.ContainerRuntime",
            side_effect=Exception("no runtime"),
        ):
            proj = resolve_project(
                std, config, project_dir=project_dir, initialize=True,
            )
        return proj

    def test_registered_project_all_ok(
        self, config_file, tmp_home, credentials_dir, capsys
    ) -> None:
        """A registered project with its shell present reports all [ok]."""
        from kanibako.errors import ContainerError

        proj = self._register_default_project(config_file, tmp_home, credentials_dir)
        assert proj.shell_path.is_dir()

        with patch(
            "kanibako.runtime.container.ContainerRuntime",
            side_effect=ContainerError("none"),
        ):
            args = argparse.Namespace(project=None, path=None)
            rc = run_box_diagnose(args)

        out = capsys.readouterr().out
        assert rc == 0
        assert "[ok] Project directory" in out
        assert "[ok] Shell directory" in out
        # The shell/project checks must not be errors (the runtime line may be
        # [!!] here because no runtime is mocked-present -- unrelated to the fix).
        assert "[!!] Project directory" not in out
        assert "[!!] Shell directory" not in out
        assert "missing or not initialized" not in out

    def test_unregistered_dir_reports_no_project(
        self, config_file, tmp_home, credentials_dir, capsys
    ) -> None:
        """A plain (non-project) directory reports no project, not a false shell error."""
        # cwd is tmp_home/project; nothing was ever registered there.
        args = argparse.Namespace(project=None, path=None)
        rc = run_box_diagnose(args)

        out = capsys.readouterr().out
        assert rc != 0
        assert "no kanibako project registered" in out
        # Must NOT emit the misleading [ok] dir + [!!] shell pair.
        assert "[ok] Project directory" not in out
        assert "Shell directory" not in out
        assert "missing or not initialized" not in out

    def test_moved_workspace_reports_no_project(
        self, config_file, tmp_home, credentials_dir, capsys
    ) -> None:
        """A copied/moved workspace not registered at its new path reports no project."""
        import shutil

        self._register_default_project(config_file, tmp_home, credentials_dir)
        moved = tmp_home / "moved"
        shutil.copytree(tmp_home / "project", moved)

        args = argparse.Namespace(project=str(moved), path=None)
        rc = run_box_diagnose(args)

        out = capsys.readouterr().out
        assert rc != 0
        assert "no kanibako project registered" in out
        assert "missing or not initialized" not in out

    def test_bare_project_name_from_other_cwd(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch
    ) -> None:
        """`box diagnose <projname>` resolves a REGISTERED project from any cwd."""
        from kanibako.errors import ContainerError

        proj = self._register_default_project(config_file, tmp_home, credentials_dir)
        name = proj.name
        # Move cwd OUT of the project so the token can only resolve by name.
        elsewhere = tmp_home / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        with patch(
            "kanibako.runtime.container.ContainerRuntime",
            side_effect=ContainerError("none"),
        ):
            args = argparse.Namespace(project=name, path=None)
            rc = run_box_diagnose(args)

        out = capsys.readouterr().out
        assert rc == 0
        assert "[ok] Project directory" in out
        # Must NOT have path-ified the bare name relative to cwd.
        assert "no kanibako project registered" not in out

    def test_bare_workset_name_errors_clearly(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch
    ) -> None:
        """`box diagnose <worksetname>` errors clearly (a workset isn't a box)."""
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths
        from kanibako.project.workset import add_project, create_workset

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("diagws", tmp_home / "ws_diag", std)
        src = tmp_home / "diag_src"
        src.mkdir()
        add_project(ws, "diagproj", src)

        elsewhere = tmp_home / "elsewhere2"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        args = argparse.Namespace(project="diagws", path=None)
        rc = run_box_diagnose(args)

        out = capsys.readouterr().out
        assert rc != 0
        # Clear, actionable message -- not the misleading "path does not exist".
        assert "is a workset" in out
        assert "does not exist" not in out

    def test_qualified_workset_project_resolves(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch
    ) -> None:
        """`box diagnose ws/proj` resolves to that project box from any cwd."""
        from kanibako.settings.config import load_config
        from kanibako.errors import ContainerError
        from kanibako.settings.paths import (
            WorksetSpec,
            load_std_paths,
            resolve_workset_project,
        )
        from kanibako.project.workset import add_project, create_workset

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("qdiag", tmp_home / "qdiag_root", std)
        internal = ws.workspaces_dir / "api"
        internal.mkdir(parents=True)
        add_project(ws, "api", internal, std)
        # Initialize the box so box.yaml is persisted (the registration the
        # diagnose guard requires).
        with patch(
            "kanibako.runtime.container.ContainerRuntime",
            side_effect=ContainerError("none"),
        ):
            resolve_workset_project(
                WorksetSpec.from_workset(ws), "api", std, config, initialize=True,
            )

        elsewhere = tmp_home / "qdiag_elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        with patch(
            "kanibako.runtime.container.ContainerRuntime",
            side_effect=ContainerError("none"),
        ):
            args = argparse.Namespace(project="qdiag/api", path=None)
            rc = run_box_diagnose(args)

        out = capsys.readouterr().out
        assert rc == 0
        assert "[ok] Project directory" in out
        # Resolved to the real workset project, not path-ified relative to cwd.
        assert "no kanibako project registered" not in out
        assert "is a workset" not in out

    def test_qualified_unknown_project_errors_clearly(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch
    ) -> None:
        """`box diagnose ws/missing` errors -- never a silent wrong path."""
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths
        from kanibako.project.workset import create_workset

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("qdiag2", tmp_home / "qdiag2_root", std)

        elsewhere = tmp_home / "qdiag2_elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        args = argparse.Namespace(project="qdiag2/nope", path=None)
        rc = run_box_diagnose(args)

        out = capsys.readouterr().out
        assert rc != 0
        # Must not have silently reported a registered project for a wrong path.
        assert "[ok] Project directory" not in out

    def test_slash_token_not_qualified_unchanged(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch
    ) -> None:
        """A non-qualified slash token still behaves as before (no regression)."""
        elsewhere = tmp_home / "slash_elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        args = argparse.Namespace(project="no/such/path", path=None)
        rc = run_box_diagnose(args)

        out = capsys.readouterr().out
        assert rc != 0
        assert "[ok] Project directory" not in out

    def test_registered_project_missing_shell_is_informational(
        self, config_file, tmp_home, credentials_dir, capsys
    ) -> None:
        """A registered project whose shell dir is absent -> informational, not error."""
        import shutil

        from kanibako.errors import ContainerError

        proj = self._register_default_project(config_file, tmp_home, credentials_dir)
        # Simulate a valid-but-not-yet-launched project: remove the shell dir.
        shutil.rmtree(proj.shell_path)
        assert not proj.shell_path.is_dir()

        with patch(
            "kanibako.runtime.container.ContainerRuntime",
            side_effect=ContainerError("none"),
        ):
            args = argparse.Namespace(project=None, path=None)
            rc = run_box_diagnose(args)

        out = capsys.readouterr().out
        assert rc == 0
        # Shell reported informationally ([--]), NOT as an error ([!!]).
        assert "[--] Shell directory" in out
        assert "not yet initialized" in out
        assert "[!!] Shell directory" not in out
        # Project directory still meaningfully [ok].
        assert "[ok] Project directory" in out

    def test_registration_verdict_from_box_resolve_not_meta_file(
        self, config_file, tmp_home, credentials_dir, capsys
    ) -> None:
        """P8a: the registration verdict is the box_resolve IDENTITY (registry
        membership), NOT the on-disk box.yaml FILE.  Removing the box's
        registry entry (identity gone) makes diagnose report 'no project' even
        though the box metadata still exists on disk.

        Mutation proof: revert diagnose to
        ``read_project_meta(...) is not None`` (the old file-present signal) →
        the still-on-disk box wrongly reports REGISTERED and this goes RED.
        """
        from kanibako.project import workset_registry
        from kanibako.settings.config import load_config
        from kanibako.settings.config_io import load_doc
        from kanibako.settings.paths import load_std_paths

        proj = self._register_default_project(
            config_file, tmp_home, credentials_dir
        )
        # The on-disk box metadata remains; only its registry membership (the
        # box_resolve identity source) is removed.
        config = load_config(config_file)
        std = load_std_paths(config)
        reg = workset_registry.resolve_workset_registry_path(
            std.primary_workset,
            load_doc(std.primary_workset / "workset.yaml"),
        )
        workset_registry.unregister_workset_box(reg, proj.name)

        args = argparse.Namespace(project=None, path=None)
        rc = run_box_diagnose(args)

        out = capsys.readouterr().out
        assert rc != 0
        assert "no kanibako project registered" in out
        assert "[ok] Project directory" not in out


class TestProbeMissingExecutables:
    """probe_missing_executables: one ephemeral run, partition the result."""

    def test_single_run_partitions_present_and_missing(self) -> None:
        from kanibako.commands.diagnose import _PROBE_HIT_PREFIX

        mock_runtime = MagicMock()
        mock_runtime.cmd = "podman"
        # The probe reports tmux + rg present, fdfind missing.
        out = f"{_PROBE_HIT_PREFIX}tmux\n{_PROBE_HIT_PREFIX}rg\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=out, stderr="")
            missing = probe_missing_executables(
                mock_runtime, "img:latest", ["tmux", "rg", "fdfind"]
            )
        assert missing == ["fdfind"]
        # Exactly ONE container spin-up for all three executables.
        assert mock_run.call_count == 1
        # Must override the image ENTRYPOINT, else kanibako-entrypoint swallows
        # the probe script and every executable looks missing.
        argv = mock_run.call_args[0][0]
        assert argv[:5] == ["podman", "run", "--rm", "--entrypoint", "sh"]
        assert argv[5] == "img:latest"

    def test_empty_list_no_run(self) -> None:
        mock_runtime = MagicMock()
        with patch("subprocess.run") as mock_run:
            assert probe_missing_executables(mock_runtime, "img", []) == []
        mock_run.assert_not_called()

    def test_runtime_failure_all_missing(self) -> None:
        mock_runtime = MagicMock()
        mock_runtime.cmd = "podman"
        with patch("subprocess.run", side_effect=OSError("boom")):
            missing = probe_missing_executables(mock_runtime, "img", ["a", "b"])
        assert missing == ["a", "b"]


class TestDiagnoseBaseline:
    """_diagnose_baseline filtering (--only/--skip) and single-vs-all images."""

    def _patch_baseline(self):
        # Three packages with one executable each.
        return patch(
            "kanibako.runtime.baseline.load_baseline",
            return_value={"tmux": ["tmux"], "ripgrep": ["rg"], "fd-find": ["fdfind"]},
        )

    def test_only_filters_packages(self, capsys) -> None:
        mock_runtime = MagicMock()
        mock_runtime.cmd = "podman"
        args = argparse.Namespace(only=["ripgrep"], skip=None, all_images=False)
        with (
            self._patch_baseline(),
            patch("kanibako.runtime.container.ContainerRuntime", return_value=mock_runtime),
            patch(
                "kanibako.settings.config.load_merged_config",
                return_value=MagicMock(box_image="img:latest"),
            ),
            patch(
                "kanibako.commands.diagnose.probe_missing_executables",
                return_value=[],
            ) as mock_probe,
        ):
            _diagnose_baseline(args)
        # Only ripgrep's exe (rg) should be probed.
        assert mock_probe.call_args[0][2] == ["rg"]

    def test_skip_filters_packages(self, capsys) -> None:
        mock_runtime = MagicMock()
        mock_runtime.cmd = "podman"
        args = argparse.Namespace(only=None, skip=["fd-find"], all_images=False)
        with (
            self._patch_baseline(),
            patch("kanibako.runtime.container.ContainerRuntime", return_value=mock_runtime),
            patch(
                "kanibako.settings.config.load_merged_config",
                return_value=MagicMock(box_image="img:latest"),
            ),
            patch(
                "kanibako.commands.diagnose.probe_missing_executables",
                return_value=[],
            ) as mock_probe,
        ):
            _diagnose_baseline(args)
        probed = mock_probe.call_args[0][2]
        assert "fdfind" not in probed
        assert set(probed) == {"tmux", "rg"}

    def test_default_single_configured_image(self, capsys) -> None:
        mock_runtime = MagicMock()
        mock_runtime.cmd = "podman"
        args = argparse.Namespace(only=None, skip=None, all_images=False)
        with (
            self._patch_baseline(),
            patch("kanibako.runtime.container.ContainerRuntime", return_value=mock_runtime),
            patch(
                "kanibako.settings.config.load_merged_config",
                return_value=MagicMock(box_image="configured:latest"),
            ),
            patch(
                "kanibako.commands.diagnose.probe_missing_executables",
                return_value=[],
            ) as mock_probe,
        ):
            _diagnose_baseline(args)
        # Single configured image probed.
        assert mock_probe.call_count == 1
        assert mock_probe.call_args[0][1] == "configured:latest"
        mock_runtime.list_local_images.assert_not_called()

    def test_all_images_probes_each(self, capsys) -> None:
        mock_runtime = MagicMock()
        mock_runtime.cmd = "podman"
        mock_runtime.list_local_images.return_value = [
            ("kanibako-oci:latest", "1GB"),
            ("kanibako-min:latest", "0.5GB"),
        ]
        args = argparse.Namespace(only=None, skip=None, all_images=True)
        with (
            self._patch_baseline(),
            patch("kanibako.runtime.container.ContainerRuntime", return_value=mock_runtime),
            patch(
                "kanibako.commands.diagnose.probe_missing_executables",
                return_value=[],
            ) as mock_probe,
        ):
            _diagnose_baseline(args)
        assert mock_probe.call_count == 2

    def test_missing_executable_reported(self, capsys) -> None:
        mock_runtime = MagicMock()
        mock_runtime.cmd = "podman"
        args = argparse.Namespace(only=None, skip=None, all_images=False)
        with (
            self._patch_baseline(),
            patch("kanibako.runtime.container.ContainerRuntime", return_value=mock_runtime),
            patch(
                "kanibako.settings.config.load_merged_config",
                return_value=MagicMock(box_image="img:latest"),
            ),
            patch(
                "kanibako.commands.diagnose.probe_missing_executables",
                return_value=["rg"],
            ),
        ):
            _diagnose_baseline(args)
        out = capsys.readouterr().out
        assert "[!!]" in out
        assert "ripgrep:rg" in out


class TestCheckVscode:
    """_check_vscode: host prerequisites for VS Code "Attach to Running Container".

    Returns three (status, label, detail) lines in a fixed order:
    [0] `code` CLI, [1] Dev Containers extension, [2] dockerPath.
    """

    def _settings(self, config_home: Path, text: str) -> Path:
        """Write a VS Code user settings.json under *config_home* and return it."""
        path = config_home / "Code" / "User" / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    # --- code CLI + extension branches -------------------------------------

    def test_code_absent(self, tmp_path: Path) -> None:
        """No `code` on PATH -> [!!] CLI + [--] extension (uncheckable)."""
        with patch("shutil.which", return_value=None):
            lines = _check_vscode(config_home=tmp_path)
        code_status, code_label, code_detail = lines[0]
        assert code_status == "!!"
        assert code_label == "VS Code CLI"
        assert "not on PATH" in code_detail
        assert "Install code command in PATH" in code_detail
        # Extension cannot be checked without the CLI.
        ext_status, _ext_label, ext_detail = lines[1]
        assert ext_status == "--"
        assert "code not on PATH" in ext_detail

    def test_code_present_ext_present(self, tmp_path: Path) -> None:
        """`code` present + Dev Containers listed -> both [ok]."""
        proc = MagicMock(
            returncode=0, stdout="ms-python.python\nms-vscode-remote.remote-containers\n"
        )
        with (
            patch("shutil.which", return_value="/usr/bin/code"),
            patch("subprocess.run", return_value=proc) as mock_run,
        ):
            lines = _check_vscode(config_home=tmp_path)
        assert lines[0][0] == "ok"
        assert lines[0][2] == "/usr/bin/code"
        assert lines[1][0] == "ok"
        assert "ms-vscode-remote.remote-containers" in lines[1][2]
        # The list-extensions probe MUST be bounded so a wedged `code` cannot
        # hang diagnose forever.
        assert mock_run.call_args.kwargs.get("timeout") == 10

    def test_code_present_ext_present_case_insensitive(self, tmp_path: Path) -> None:
        """Extension match is case-insensitive."""
        proc = MagicMock(returncode=0, stdout="MS-VSCode-Remote.Remote-Containers\n")
        with (
            patch("shutil.which", return_value="/usr/bin/code"),
            patch("subprocess.run", return_value=proc),
        ):
            lines = _check_vscode(config_home=tmp_path)
        assert lines[1][0] == "ok"

    def test_code_present_ext_missing(self, tmp_path: Path) -> None:
        """`code` present but Dev Containers NOT listed -> [!!] with remediation."""
        proc = MagicMock(returncode=0, stdout="ms-python.python\n")
        with (
            patch("shutil.which", return_value="/usr/bin/code"),
            patch("subprocess.run", return_value=proc),
        ):
            lines = _check_vscode(config_home=tmp_path)
        ext_status, _label, ext_detail = lines[1]
        assert ext_status == "!!"
        assert "not installed" in ext_detail
        assert "code --install-extension ms-vscode-remote.remote-containers" in ext_detail

    def test_code_present_list_extensions_nonzero(self, tmp_path: Path) -> None:
        """A non-zero `code --list-extensions` degrades to [--], never crashes."""
        proc = MagicMock(returncode=1, stdout="")
        with (
            patch("shutil.which", return_value="/usr/bin/code"),
            patch("subprocess.run", return_value=proc),
        ):
            lines = _check_vscode(config_home=tmp_path)
        assert lines[1][0] == "--"
        assert "failed" in lines[1][2]

    def test_code_present_list_extensions_raises(self, tmp_path: Path) -> None:
        """A raising `code --list-extensions` degrades to [--], never crashes."""
        with (
            patch("shutil.which", return_value="/usr/bin/code"),
            patch("subprocess.run", side_effect=OSError("boom")),
        ):
            lines = _check_vscode(config_home=tmp_path)
        assert lines[1][0] == "--"
        assert "failed" in lines[1][2]

    def test_code_present_list_extensions_hangs_times_out(
        self, tmp_path: Path
    ) -> None:
        """A wedged `code` (TimeoutExpired) degrades to [--] -- diagnose must
        never hang.  subprocess.run is called with timeout=; TimeoutExpired
        subclasses Exception so it routes to the honest cannot-check line."""
        import subprocess

        with (
            patch("shutil.which", return_value="/usr/bin/code"),
            patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(
                    cmd="code --list-extensions", timeout=10
                ),
            ),
        ):
            lines = _check_vscode(config_home=tmp_path)
        assert lines[1][0] == "--"
        assert "failed" in lines[1][2]

    # --- dockerPath / settings.json branches -------------------------------

    def _vscode_with_settings(self, config_home: Path):
        """Patch code-absent (irrelevant here) and return the dockerPath line."""
        with patch("shutil.which", return_value=None):
            return _check_vscode(config_home=config_home)[2]

    def test_settings_absent(self, tmp_path: Path) -> None:
        """No settings.json -> [!!] with remediation and the probed path."""
        status, label, detail = self._vscode_with_settings(tmp_path)
        assert status == "!!"
        assert label == "VS Code dockerPath"
        assert "not found" in detail
        assert 'dev.containers.dockerPath": "podman"' in detail
        assert str(tmp_path / "Code" / "User" / "settings.json") in detail

    def test_dockerpath_podman_ok(self, tmp_path: Path) -> None:
        """dockerPath == podman -> [ok] with a local-only note (FF-1 widening)."""
        self._settings(tmp_path, '{"dev.containers.dockerPath": "podman"}')
        status, _label, detail = self._vscode_with_settings(tmp_path)
        assert status == "ok"
        assert "podman" in detail
        assert "local only" in detail
        assert "--remote" in detail

    def test_dockerpath_kanibako_wrapper_ok(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        """dockerPath == the kanibako dispatch wrapper path -> [ok] (FF-1)."""
        from kanibako.vscode import vscode_remote as vr

        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        wrapper = str(vr.dispatch_wrapper_path())
        self._settings(
            tmp_path,
            json.dumps({"dev.containers.dockerPath": wrapper}),
        )
        status, _label, detail = self._vscode_with_settings(tmp_path)
        assert status == "ok"
        assert "kanibako dispatch wrapper" in detail

    def test_dockerpath_other_value(self, tmp_path: Path) -> None:
        """dockerPath set to something else -> [!!] naming the wrong value."""
        self._settings(tmp_path, '{"dev.containers.dockerPath": "docker"}')
        status, _label, detail = self._vscode_with_settings(tmp_path)
        assert status == "!!"
        assert "docker" in detail
        assert "expected" in detail

    def test_dockerpath_key_missing(self, tmp_path: Path) -> None:
        """Valid settings.json but no dockerPath key -> [!!] 'not set'."""
        self._settings(tmp_path, '{"editor.fontSize": 14}')
        status, _label, detail = self._vscode_with_settings(tmp_path)
        assert status == "!!"
        assert "not set" in detail

    def test_settings_jsonc_comments_and_trailing_comma(self, tmp_path: Path) -> None:
        """JSONC (comments + trailing comma) is parsed via the strip fallback."""
        self._settings(
            tmp_path,
            """{
                // line comment
                "editor.fontSize": 14, /* block */
                "dev.containers.dockerPath": "podman",
            }""",
        )
        status, _label, _detail = self._vscode_with_settings(tmp_path)
        assert status == "ok"

    def test_settings_url_value_not_clobbered_by_strip(self, tmp_path: Path) -> None:
        """A `//` inside a string value must NOT be treated as a comment."""
        self._settings(
            tmp_path,
            """{
                "some.url": "http://example.com",
                "dev.containers.dockerPath": "podman",
            }""",
        )
        status, _label, _detail = self._vscode_with_settings(tmp_path)
        assert status == "ok"

    def test_settings_unparseable(self, tmp_path: Path) -> None:
        """Genuinely broken JSON -> [--] 'could not be parsed', never crashes."""
        self._settings(tmp_path, "{ this is not json at all ][ ")
        status, _label, detail = self._vscode_with_settings(tmp_path)
        assert status == "--"
        assert "could not be parsed" in detail

    def test_default_config_home_resolution(self, tmp_path: Path, monkeypatch) -> None:
        """Without config_home, the XDG_CONFIG_HOME resolution is used."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        self._settings(tmp_path, '{"dev.containers.dockerPath": "podman"}')
        with patch("shutil.which", return_value=None):
            lines = _check_vscode()
        assert lines[2][0] == "ok"

    # --- wiring into run_system_diagnose -----------------------------------

    def test_system_diagnose_includes_vscode_section(
        self, config_file, tmp_home, credentials_dir, capsys
    ) -> None:
        """run_system_diagnose prints the VS Code lines."""
        from kanibako.errors import ContainerError

        with (
            patch(
                "kanibako.runtime.container.ContainerRuntime",
                side_effect=ContainerError("none"),
            ),
            patch("shutil.which", return_value=None),
        ):
            rc = run_system_diagnose(argparse.Namespace())
        out = capsys.readouterr().out
        assert rc == 0
        assert "VS Code CLI" in out
        assert "VS Code dockerPath" in out


class TestParsers:
    """Verify that diagnose subcommands are parseable."""

    def test_rig_diagnose_baseline_flags(self) -> None:
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(
            ["rig", "diagnose", "--all", "--only", "ripgrep", "--skip", "fd-find"]
        )
        assert args.func == run_rig_diagnose
        assert args.all_images is True
        assert args.only == ["ripgrep"]
        assert args.skip == ["fd-find"]

    def test_system_diagnose_parser(self) -> None:
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["system", "diagnose"])
        assert args.func == run_system_diagnose

    def test_rig_diagnose_parser(self) -> None:
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["rig", "diagnose"])
        assert args.func == run_rig_diagnose

    def test_box_diagnose_parser(self) -> None:
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["box", "diagnose"])
        assert args.func == run_box_diagnose

    def test_box_diagnose_with_project(self) -> None:
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["box", "diagnose", "myproject"])
        assert args.func == run_box_diagnose
        assert args.project == "myproject"


@pytest.mark.writes_undeclared(
    "box.frobnicate",
    reason="the refusal these tests drive is the §0 read gate itself, so the "
           "settings file they write has to carry an undeclared key; the write "
           "happens inside the resolve that then refuses it.",
)
class TestSettingsRefusalIsSurfaced:
    """A settings error must be NAMED, never reported as `(not configured)`.

    The spec §0 read gate makes an undeclared key a refusal that names the
    offending entry and every file the resolve loaded.  `diagnose` is what a
    user runs when something is wrong, so swallowing that refusal into
    "cannot check (not configured)" points them at the wrong cause.

    These go through the REAL resolve -- an undeclared entry written into the
    real system-tier settings file -- not a patched exception, so they fail if
    the refusal stops reaching `load_merged_config` at all.
    """

    UNDECLARED = "frobnicate"

    def _write_undeclared_key(self, config_file: Path) -> Path:
        """Put an undeclared `box.frobnicate` in the system-tier settings file."""
        from kanibako.settings.paths import load_system_config, xdg

        settings_path = load_system_config(
            config_file,
            data_home=xdg("XDG_DATA_HOME", ".local/share"),
            home=Path.home(),
        )["config.settings"]
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(f"box:\n  {self.UNDECLARED}: yes\n")
        return settings_path

    def _assert_names_the_key(self, out: str, settings_path: Path) -> None:
        """The refusal reached the user intact: key, spec cite, and file list."""
        assert "[!!]" in out
        assert "settings error -- reported below" in out
        assert f"box.{self.UNDECLARED}" in out
        assert "spec §0" in out
        assert str(settings_path) in out
        assert "cannot check (not configured)" not in out

    def test_system_diagnose_surfaces_the_refusal(
        self, config_file, tmp_home, credentials_dir, capsys
    ) -> None:
        """`kanibako system diagnose` names the undeclared key on the Image line."""
        from kanibako.errors import ContainerError

        settings_path = self._write_undeclared_key(config_file)
        with patch(
            "kanibako.runtime.container.ContainerRuntime",
            side_effect=ContainerError("none"),
        ):
            rc = run_system_diagnose(argparse.Namespace())
        assert rc == 0
        out = capsys.readouterr().out
        self._assert_names_the_key(out, settings_path)
        assert "[!!] Image: settings error -- reported below" in out

    def test_rig_diagnose_surfaces_the_refusal(
        self, config_file, tmp_home, credentials_dir, capsys
    ) -> None:
        """`kanibako rig diagnose` names it on the Configured image line."""
        from kanibako.errors import ContainerError

        settings_path = self._write_undeclared_key(config_file)
        with patch(
            "kanibako.runtime.container.ContainerRuntime",
            side_effect=ContainerError("none"),
        ):
            rc = run_rig_diagnose(argparse.Namespace())
        assert rc == 0
        out = capsys.readouterr().out
        self._assert_names_the_key(out, settings_path)
        assert "[!!] Configured image: settings error -- reported below" in out
        assert "Configured image: cannot check" not in out

    def test_baseline_surfaces_the_refusal(
        self, config_file, tmp_home, credentials_dir, capsys
    ) -> None:
        """The baseline probe names it instead of claiming nothing is configured."""
        mock_runtime = MagicMock()
        mock_runtime.cmd = "podman"
        settings_path = self._write_undeclared_key(config_file)
        args = argparse.Namespace(only=None, skip=None, all_images=False)
        with (
            patch(
                "kanibako.runtime.baseline.load_baseline",
                return_value={"tmux": ["tmux"]},
            ),
            patch(
                "kanibako.runtime.container.ContainerRuntime",
                return_value=mock_runtime,
            ),
            patch(
                "kanibako.commands.diagnose.probe_missing_executables",
                return_value=[],
            ) as mock_probe,
        ):
            _diagnose_baseline(args)
        out = capsys.readouterr().out
        self._assert_names_the_key(out, settings_path)
        assert "[!!]   Baseline: settings error -- reported below" in out
        # The refusal ends the probe -- there is no resolved image to probe.
        mock_probe.assert_not_called()

    def test_non_kanibako_failure_still_reports_not_configured(
        self, config_file, tmp_home, credentials_dir, capsys
    ) -> None:
        """The `(not configured)` line survives for what it was written for."""
        from kanibako.errors import ContainerError

        with (
            patch(
                "kanibako.runtime.container.ContainerRuntime",
                side_effect=ContainerError("none"),
            ),
            patch(
                "kanibako.settings.config.load_merged_config",
                side_effect=RuntimeError("boom"),
            ),
        ):
            rc = run_system_diagnose(argparse.Namespace())
        assert rc == 0
        out = capsys.readouterr().out
        assert "[--] Image: cannot check (not configured)" in out
        assert "settings error -- reported below" not in out

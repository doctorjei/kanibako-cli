"""Tests for kanibako.cli main() exit codes and standalone launch."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from kanibako.errors import KanibakoError, UserCanceled
from kanibako.settings.paths import ProjectGroup, BoxMode

from tests.support.filenames import CONFIG_FILENAME


class TestMainExitCodes:
    def test_user_canceled_exits_2(self):
        from kanibako.cli import main

        with (
            patch("kanibako.cli.build_parser") as mock_parser,
            patch("kanibako.cli._ensure_initialized"),
            patch("kanibako.cli._setup_nudge"),
            pytest.raises(SystemExit) as exc_info,
        ):
            args = MagicMock()
            args.command = "start"
            args.func.side_effect = UserCanceled("nope")
            mock_parser.return_value.parse_args.return_value = args
            main(["start"])
        assert exc_info.value.code == 2

    def test_kanibako_error_exits_1(self):
        from kanibako.cli import main

        with (
            patch("kanibako.cli.build_parser") as mock_parser,
            patch("kanibako.cli._ensure_initialized"),
            patch("kanibako.cli._setup_nudge"),
            pytest.raises(SystemExit) as exc_info,
        ):
            args = MagicMock()
            args.command = "start"
            args.func.side_effect = KanibakoError("boom")
            mock_parser.return_value.parse_args.return_value = args
            main(["start"])
        assert exc_info.value.code == 1

    def test_keyboard_interrupt_exits_130(self):
        from kanibako.cli import main

        with (
            patch("kanibako.cli.build_parser") as mock_parser,
            patch("kanibako.cli._ensure_initialized"),
            patch("kanibako.cli._setup_nudge"),
            pytest.raises(SystemExit) as exc_info,
        ):
            args = MagicMock()
            args.command = "start"
            args.func.side_effect = KeyboardInterrupt()
            mock_parser.return_value.parse_args.return_value = args
            main(["start"])
        assert exc_info.value.code == 130

    def test_success_exits_0(self):
        from kanibako.cli import main

        with (
            patch("kanibako.cli.build_parser") as mock_parser,
            patch("kanibako.cli._ensure_initialized"),
            patch("kanibako.cli._setup_nudge"),
            pytest.raises(SystemExit) as exc_info,
        ):
            args = MagicMock()
            args.command = "start"
            args.func.return_value = 0
            mock_parser.return_value.parse_args.return_value = args
            main(["start"])
        assert exc_info.value.code == 0

    def test_nonzero_propagation(self):
        from kanibako.cli import main

        with (
            patch("kanibako.cli.build_parser") as mock_parser,
            patch("kanibako.cli._ensure_initialized"),
            patch("kanibako.cli._setup_nudge"),
            pytest.raises(SystemExit) as exc_info,
        ):
            args = MagicMock()
            args.command = "start"
            args.func.return_value = 42
            mock_parser.return_value.parse_args.return_value = args
            main(["start"])
        assert exc_info.value.code == 42

    def test_no_command_defaults_to_start(self):
        """When no command given, main() prepends 'start' and parses once."""
        from kanibako.cli import main

        with (
            patch("kanibako.cli.build_parser") as mock_bp,
            patch("kanibako.cli._ensure_initialized"),
            patch("kanibako.cli._setup_nudge"),
            pytest.raises(SystemExit) as exc_info,
        ):
            parser = MagicMock()
            mock_bp.return_value = parser

            with_cmd = MagicMock()
            with_cmd.command = "start"
            with_cmd.func.return_value = 0
            parser.parse_args.return_value = with_cmd

            main([])
        assert exc_info.value.code == 0
        parser.parse_args.assert_called_once_with(["start"])


class TestLazyInit:
    def test_missing_config_triggers_lazy_init(self, tmp_path, monkeypatch):
        """Running a command without config file creates it via lazy init."""
        from kanibako.cli import _ensure_initialized

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        (tmp_path / "home").mkdir(parents=True, exist_ok=True)

        _ensure_initialized()

        config_file = tmp_path / "config" / CONFIG_FILENAME
        assert config_file.exists()
        # Data directories should also be created
        assert (tmp_path / "data" / "kanibako" / "containers").is_dir()
        assert (tmp_path / "data" / "kanibako" / "agents").is_dir()
        # NEGATIVE SPACE (cli.py NOTE block #3a / JC-3): the channel type-root
        # skeleton is NOT pre-created host-side.  The launch path owns it (the L7
        # guarantee-create for the bind sources + ``_seed_channel_files``), and no
        # host-side pre-launch consumer exists.  Asserted on the LIVE init path
        # after the dead ``commands/install.run`` test that used to cover it was
        # deleted (Phase 0).
        channels = tmp_path / "data" / "kanibako" / "channels"
        assert not (channels / "common").exists()
        assert not (channels / "chat" / "general.md").exists()

    def test_lazy_init_idempotent(self, tmp_path, monkeypatch):
        """Running lazy init twice does not error or overwrite config."""
        from kanibako.cli import _ensure_initialized
        from kanibako.settings.config import load_config, write_global_config
        from kanibako.settings.config_io import write_nested_key

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        (tmp_path / "home").mkdir(parents=True, exist_ok=True)

        # Write custom config first.
        # ⚑ A ``config.*`` value, not ``box.image``: since 2026-08-26 the Layer-1 file
        # carries the bootstrap foundation and nothing else (Jei), so a settings key
        # planted here would be inert and the test would pass without meaning it.
        config_file = tmp_path / "config" / CONFIG_FILENAME
        config_file.parent.mkdir(parents=True, exist_ok=True)
        write_global_config(config_file)
        write_nested_key(config_file, ("config",), "agents", "/custom/agents")

        _ensure_initialized()

        # Custom config should be preserved
        loaded = load_config(config_file)
        assert loaded.config_paths["config.agents"] == "/custom/agents"

    def test_first_run_layer2_path_refusal_exits_1_without_traceback(self, tmp_home, capsys):
        """A first-run ``null`` ``system.*`` path key is a clean rc1 refusal, not a traceback.

        MUTATION: move ``_ensure_initialized()`` back out of ``main``'s ``KanibakoError``
        handler and the ConfigError escapes ``main`` instead of a ``SystemExit(1)``.
        """
        from kanibako.cli import main

        settings = tmp_home / "data" / "kanibako" / "global" / "settings.yaml"
        settings.parent.mkdir(parents=True)
        settings.write_text("system:\n  state: null\n")

        with (
            patch("kanibako.cli.build_parser") as mock_bp,
            patch("kanibako.cli._setup_nudge"),
            pytest.raises(SystemExit) as exc_info,
        ):
            args = MagicMock()
            args.command = "box"
            args.box_command = "list"
            mock_bp.return_value.parse_args.return_value = args
            main(["box", "list"])
        assert exc_info.value.code == 1
        args.func.assert_not_called()
        err = capsys.readouterr().err
        assert f"Error: {settings} sets these path keys to null:" in err
        assert "system.state" in err
        assert "Traceback" not in err

    def test_refused_first_run_leaves_no_config_so_next_run_initializes(self, tmp_home, capsys):
        """A first-run refusal leaves no kanibako.cfg; fixing settings.yaml lets the next run initialize.

        Regression test: before the fix, _ensure_initialized() wrote kanibako.cfg BEFORE calling
        load_std_paths(), so a ConfigError on first run left the file on disk.  The next run then
        returned early at the "already initialized" check and never installed templates or shell
        completion.
        """
        from kanibako.cli import main
        from kanibako.launch.templates import PACKAGED_BOX_TEMPLATE

        settings = tmp_home / "data" / "kanibako" / "global" / "settings.yaml"
        settings.parent.mkdir(parents=True)
        settings.write_text("system:\n  state: null\n")

        config_file = tmp_home / "config" / CONFIG_FILENAME

        # First run: refused with exit 1, no config file left behind.
        with (
            patch("kanibako.cli.build_parser") as mock_bp,
            patch("kanibako.cli._setup_nudge"),
            pytest.raises(SystemExit) as exc_info,
        ):
            args = MagicMock()
            args.command = "box"
            args.box_command = "list"
            mock_bp.return_value.parse_args.return_value = args
            main(["box", "list"])
        assert exc_info.value.code == 1
        assert not config_file.exists(), "kanibako.cfg must not exist after a refused first run"

        # Fix the bad settings key.
        settings.write_text("system:\n  state: ~/.local/state/kanibako\n")

        # Second run: succeeds and creates the config file AND installs templates.
        install_completion = patch("kanibako.commands.install._install_completion")
        with (
            patch("kanibako.cli.build_parser") as mock_bp,
            patch("kanibako.cli._setup_nudge"),
            install_completion as mock_completion,
            pytest.raises(SystemExit) as exc_info,
        ):
            args = MagicMock()
            args.command = "box"
            args.box_command = "list"
            args.func.return_value = 0
            mock_bp.return_value.parse_args.return_value = args
            main(["box", "list"])
        assert exc_info.value.code == 0
        assert config_file.exists(), "kanibako.cfg must exist after a successful run"
        # Must have called completion setup and installed at least one packaged template.
        mock_completion.assert_called_once()
        # std.template resolves to .../kanibako/global/template (system.template path).
        data_kanibako = tmp_home / "data" / "kanibako"
        assert (data_kanibako / "global" / "template" / PACKAGED_BOX_TEMPLATE).exists(), \
            "packaged box template must be installed after successful init"

    def test_oserror_during_init_leaves_no_config(self, tmp_home, capsys):
        """Any exception during init removes the config file so the next run retries.

        Regression test for fix 4: install_packaged_templates (or any step after
        write_global_config) raising OSError must also clean up the config file,
        not just ConfigError.
        """
        from kanibako.cli import main

        config_file = tmp_home / "config" / CONFIG_FILENAME

        with (
            patch("kanibako.cli.build_parser") as mock_bp,
            patch("kanibako.cli._setup_nudge"),
            patch("kanibako.launch.templates.install_packaged_templates",
                  side_effect=OSError("simulated disk error")),
            pytest.raises(SystemExit) as exc_info,
        ):
            args = MagicMock()
            args.command = "box"
            args.box_command = "list"
            mock_bp.return_value.parse_args.return_value = args
            main(["box", "list"])
        assert exc_info.value.code == 1
        assert not config_file.exists(), \
            "kanibako.cfg must not exist after an OSError during init"

    def test_interrupt_during_init_exits_130_and_leaves_no_config(
        self, tmp_home, capsys
    ):
        """A Ctrl-C during first run is a clean rc130 that leaves no config behind.

        Pinned: the interrupt is mapped to the same newline + 130 the verb handler
        produces, no traceback reaches stderr, ``kanibako.cfg`` is removed, and the
        next run initializes from scratch.
        """
        from kanibako.cli import main
        from kanibako.launch.templates import PACKAGED_BOX_TEMPLATE

        config_file = tmp_home / "config" / CONFIG_FILENAME

        with (
            patch("kanibako.cli.build_parser") as mock_bp,
            patch("kanibako.cli._setup_nudge"),
            patch("kanibako.launch.templates.install_packaged_templates",
                  side_effect=KeyboardInterrupt()),
            pytest.raises(SystemExit) as exc_info,
        ):
            args = MagicMock()
            args.command = "box"
            args.box_command = "list"
            mock_bp.return_value.parse_args.return_value = args
            main(["box", "list"])
        assert exc_info.value.code == 130
        args.func.assert_not_called()
        assert "Traceback" not in capsys.readouterr().err
        assert not config_file.exists(), \
            "kanibako.cfg must not exist after an interrupt during init"

        # Next run: un-interrupted, so the first run completes and installs templates.
        install_completion = patch("kanibako.commands.install._install_completion")
        with (
            patch("kanibako.cli.build_parser") as mock_bp,
            patch("kanibako.cli._setup_nudge"),
            install_completion as mock_completion,
            pytest.raises(SystemExit) as exc_info,
        ):
            args = MagicMock()
            args.command = "box"
            args.box_command = "list"
            args.func.return_value = 0
            mock_bp.return_value.parse_args.return_value = args
            main(["box", "list"])
        assert exc_info.value.code == 0
        assert config_file.exists(), \
            "kanibako.cfg must exist after the run following an interrupt"
        mock_completion.assert_called_once()
        data_kanibako = tmp_home / "data" / "kanibako"
        assert (data_kanibako / "global" / "template" / PACKAGED_BOX_TEMPLATE).exists(), \
            "packaged box template must be installed after successful init"

    def test_oserror_in_early_init_step_leaves_no_config(self, tmp_home):
        """An OSError in an EARLY init step (discover_targets) also removes kanibako.cfg.

        Regression test: the cleanup handler used to start at ``load_std_paths()``, so
        the steps between ``write_global_config(cf)`` and that call ran unprotected.  A
        failure in one of those — here ``discover_targets()`` — raised AND left
        ``kanibako.cfg`` on disk, so the next run returned early at the "already
        initialized" check and never installed templates or shell completion.
        """
        from kanibako.cli import main

        config_file = tmp_home / "config" / CONFIG_FILENAME

        # Patch where the name is DEFINED: _ensure_initialized imports it inside the
        # function, so patching kanibako.cli would do nothing.
        with (
            patch("kanibako.cli.build_parser") as mock_bp,
            patch("kanibako.cli._setup_nudge"),
            patch("kanibako.targets.discover_targets",
                  side_effect=OSError("simulated disk error")),
            pytest.raises(SystemExit) as exc_info,
        ):
            args = MagicMock()
            args.command = "box"
            args.box_command = "list"
            mock_bp.return_value.parse_args.return_value = args
            main(["box", "list"])
        assert exc_info.value.code == 1
        assert not config_file.exists(), \
            "kanibako.cfg must not exist after an OSError in an early init step"

    def test_agent_exempt_from_lazy_init(self):
        """'agent' command does not trigger lazy init."""
        from kanibako.cli import main

        with (
            patch("kanibako.cli.build_parser") as mock_bp,
            patch("kanibako.cli._ensure_initialized") as mock_init,
            pytest.raises(SystemExit) as exc_info,
        ):
            args = MagicMock()
            args.command = "agent"
            args.func.return_value = 0
            mock_bp.return_value.parse_args.return_value = args
            main(["agent"])
        assert exc_info.value.code == 0
        mock_init.assert_not_called()

    def test_box_helper_exempt_from_lazy_init(self):
        """'box helper' command does not trigger lazy init."""
        from kanibako.cli import main

        with (
            patch("kanibako.cli.build_parser") as mock_bp,
            patch("kanibako.cli._ensure_initialized") as mock_init,
            pytest.raises(SystemExit) as exc_info,
        ):
            args = MagicMock()
            args.command = "box"
            args.box_command = "helper"
            args.func.return_value = 0
            mock_bp.return_value.parse_args.return_value = args
            main(["box", "helper", "list"])
        assert exc_info.value.code == 0
        mock_init.assert_not_called()


class TestStandaloneLaunch:
    """Tests for standalone project detection and launch (Phase 8.3)."""

    def _make_standalone_proj(self, project_path):
        """Build a MagicMock ProjectPaths for standalone mode."""
        proj = MagicMock()
        proj.is_new = False
        proj.mode = BoxMode.standalone
        proj.group = None  # standalone belongs to no group
        proj.project_path = project_path
        proj.project_hash = "abc123"
        proj.metadata_path = project_path / ".kanibako"
        proj.shell_path = project_path / ".kanibako" / "shell"
        proj.vault_ro_path = project_path / "vault" / "ro"
        proj.vault_rw_path = project_path / "vault" / "rw"
        # A real materialized box always has its workspace + home on disk;
        # the launch-time integrity gate (_check_box_components) requires it.
        proj.project_path.mkdir(parents=True, exist_ok=True)
        proj.shell_path.mkdir(parents=True, exist_ok=True)
        return proj

    def test_start_detects_standalone_project(self, start_mocks, tmp_path):
        """start from a standalone project dir uses resolve_any_project."""
        from kanibako.commands.start import _run_container

        project = tmp_path / "myproject"
        project.mkdir()
        (project / ".kanibako").mkdir()

        with start_mocks() as m:
            proj = self._make_standalone_proj(project)
            m.resolve_any_project.return_value = proj

            rc = _run_container(
                project_dir=str(project), entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )

        assert rc == 0
        m.resolve_any_project.assert_called()  # explicit-create gate probes existence, then resolves

    def test_start_standalone_creates_lock(self, start_mocks, tmp_path):
        """kanibako/.kanibako.lock is used during run."""
        from kanibako.commands.start import _run_container

        project = tmp_path / "myproject"
        project.mkdir()
        kanibako_dir = project / ".kanibako"
        kanibako_dir.mkdir()

        with start_mocks() as m:
            proj = self._make_standalone_proj(project)
            m.resolve_any_project.return_value = proj

            rc = _run_container(
                project_dir=str(project), entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )

        assert rc == 0
        # The lock file path derives from proj.metadata_path / ".kanibako.lock"
        # which is project/kanibako/.kanibako.lock
        m.fcntl.flock.assert_called()

    def test_start_standalone_passes_correct_paths(self, start_mocks, tmp_path):
        """runtime.run() receives paths inside the project dir."""
        from kanibako.commands.start import _run_container

        project = tmp_path / "myproject"
        project.mkdir()
        (project / ".kanibako").mkdir()

        with start_mocks() as m:
            proj = self._make_standalone_proj(project)
            m.resolve_any_project.return_value = proj

            _run_container(
                project_dir=str(project), entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )

        call_kwargs = m.runtime.run.call_args.kwargs
        assert call_kwargs["shell_path"] == project / ".kanibako" / "shell"
        assert call_kwargs["project_path"] == project
        assert call_kwargs["vault_ro_path"] == project / "vault" / "ro"
        assert call_kwargs["vault_rw_path"] == project / "vault" / "rw"

    def test_start_standalone_credential_flow(self, start_mocks, tmp_path):
        """A standalone box shares at the GLOBAL tier → cred hooks DO run.

        Under the auth 3-tier SHARING model (2026-07-01 redesign) a standalone
        box has no WORKSET group, so the workset tier degenerates false — but the
        GLOBAL tier is still enabled by default (``box.auth.global_enabled``), so
        the resolved :class:`AuthSource` has ``tier == "global"`` and
        ``auth_src.creds_shared`` is True.  The ``if target and auth_src.creds_shared``
        cred-refresh / writeback gates therefore FIRE (source = the host home).
        (The prior "standalone short-circuits auth OFF" behavior was removed with
        the boolean group_auth chain.)
        """
        from kanibako.commands.start import _run_container

        project = tmp_path / "myproject"
        project.mkdir()
        (project / ".kanibako").mkdir()

        with start_mocks() as m:
            # Pin the legacy credential-hook path: a descriptor-bearing target
            # routes refresh/writeback through the credsync engine instead.
            m.target.descriptor = None
            proj = self._make_standalone_proj(project)
            m.resolve_any_project.return_value = proj

            _run_container(
                project_dir=str(project), entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )

        # Standalone shares at the global tier → both cred hooks run.
        m.target.refresh_credentials.assert_called()
        m.target.writeback_credentials.assert_called()

    def test_shell_works_with_standalone(self, start_mocks, tmp_path):
        """shell auto-detects standalone mode via resolve_any_project."""
        from kanibako.commands.start import run_shell

        import argparse
        args = argparse.Namespace(project=str(tmp_path), entrypoint=None)
        (tmp_path / ".kanibako").mkdir()

        with start_mocks() as m:
            proj = self._make_standalone_proj(tmp_path)
            m.resolve_any_project.return_value = proj

            rc = run_shell(args)

        assert rc == 0
        m.resolve_any_project.assert_called()  # explicit-create gate probes existence, then resolves

    def test_resume_works_with_standalone(self, start_mocks, tmp_path):
        """start -R auto-detects standalone mode via resolve_any_project."""
        from kanibako.commands.start import _run_container

        (tmp_path / ".kanibako").mkdir()

        with start_mocks() as m:
            proj = self._make_standalone_proj(tmp_path)
            m.resolve_any_project.return_value = proj

            rc = _run_container(
                project_dir=str(tmp_path), entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=True,
                extra_args=[],
            )

        assert rc == 0
        m.resolve_any_project.assert_called()  # explicit-create gate probes existence, then resolves

    def test_start_local_still_works(self, start_mocks, tmp_path):
        """Non-standalone dir falls through to local."""
        from kanibako.commands.start import _run_container

        project = tmp_path / "regular_project"
        project.mkdir()

        with start_mocks() as m:
            # Default start_mocks proj is local
            rc = _run_container(
                project_dir=str(project), entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )

        assert rc == 0
        m.resolve_any_project.assert_called()  # explicit-create gate probes existence, then resolves

    # ``test_start_standalone_no_orphan_hint`` removed with the launch-path
    # orphan hint itself (MBR-6 residual 2) — see the note in
    # ``tests/test_commands/test_start_extended.py``.


class TestWorksetLaunch:
    """Tests for workset project detection and launch (Phase 7.5)."""

    def _make_workset_proj(self, ws_root, project_name):
        """Build a MagicMock ProjectPaths for workset mode."""
        proj = MagicMock()
        proj.is_new = False
        proj.mode = BoxMode.named
        proj.group = ProjectGroup(
            name="my-workset", root=ws_root,
            is_default=False, local_shared_base=ws_root,
        )
        proj.project_path = ws_root / "workspaces" / project_name
        proj.project_hash = "ws123abc"
        proj.metadata_path = ws_root / "kanibako" / project_name
        proj.shell_path = ws_root / "kanibako" / project_name / "shell"
        proj.vault_ro_path = ws_root / "vault" / project_name / "ro"
        proj.vault_rw_path = ws_root / "vault" / project_name / "rw"
        # A real materialized box always has its workspace + home on disk;
        # the launch-time integrity gate (_check_box_components) requires it.
        proj.project_path.mkdir(parents=True, exist_ok=True)
        proj.shell_path.mkdir(parents=True, exist_ok=True)
        return proj

    def test_start_detects_workset_project(self, start_mocks, tmp_path):
        """start from inside a workset workspace returns rc=0."""
        from kanibako.commands.start import _run_container

        ws_root = tmp_path / "my-workset"
        ws_root.mkdir()
        workspace = ws_root / "workspaces" / "myproj"
        workspace.mkdir(parents=True)

        with start_mocks() as m:
            proj = self._make_workset_proj(ws_root, "myproj")
            m.resolve_any_project.return_value = proj

            rc = _run_container(
                project_dir=str(workspace), entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )

        assert rc == 0
        m.resolve_any_project.assert_called()  # explicit-create gate probes existence, then resolves

    def test_start_workset_creates_lock(self, start_mocks, tmp_path):
        """projects/{name}/.kanibako.lock is used during run."""
        from kanibako.commands.start import _run_container

        ws_root = tmp_path / "my-workset"
        ws_root.mkdir()
        workspace = ws_root / "workspaces" / "myproj"
        workspace.mkdir(parents=True)

        with start_mocks() as m:
            proj = self._make_workset_proj(ws_root, "myproj")
            m.resolve_any_project.return_value = proj

            rc = _run_container(
                project_dir=str(workspace), entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )

        assert rc == 0
        m.fcntl.flock.assert_called()

    def test_start_workset_passes_correct_paths(self, start_mocks, tmp_path):
        """runtime.run() receives name-based workset paths (not hash-based)."""
        from kanibako.commands.start import _run_container

        ws_root = tmp_path / "my-workset"
        ws_root.mkdir()
        workspace = ws_root / "workspaces" / "myproj"
        workspace.mkdir(parents=True)

        with start_mocks() as m:
            proj = self._make_workset_proj(ws_root, "myproj")
            m.resolve_any_project.return_value = proj

            _run_container(
                project_dir=str(workspace), entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )

        call_kwargs = m.runtime.run.call_args.kwargs
        assert call_kwargs["shell_path"] == ws_root / "kanibako" / "myproj" / "shell"
        assert call_kwargs["project_path"] == ws_root / "workspaces" / "myproj"
        assert call_kwargs["vault_ro_path"] == ws_root / "vault" / "myproj" / "ro"
        assert call_kwargs["vault_rw_path"] == ws_root / "vault" / "myproj" / "rw"

    def test_start_workset_credential_flow(self, start_mocks, tmp_path):
        """Credential refresh uses target with workset shell_path."""
        from kanibako.commands.start import _run_container

        ws_root = tmp_path / "my-workset"
        ws_root.mkdir()
        workspace = ws_root / "workspaces" / "myproj"
        workspace.mkdir(parents=True)

        with start_mocks() as m:
            # Pin the legacy credential-hook path: a descriptor-bearing target
            # routes refresh/writeback through the credsync engine instead.
            m.target.descriptor = None
            proj = self._make_workset_proj(ws_root, "myproj")
            m.resolve_any_project.return_value = proj

            _run_container(
                project_dir=str(workspace), entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )

        # target.refresh_credentials called with shell_path
        m.target.refresh_credentials.assert_called_once_with(
            ws_root / "kanibako" / "myproj" / "shell"
        )

        # target.writeback_credentials called with shell_path
        m.target.writeback_credentials.assert_called_once_with(
            ws_root / "kanibako" / "myproj" / "shell"
        )

    def test_shell_works_with_workset(self, start_mocks, tmp_path):
        """shell auto-detects workset mode via resolve_any_project."""
        from kanibako.commands.start import run_shell

        import argparse
        ws_root = tmp_path / "my-workset"
        ws_root.mkdir()
        workspace = ws_root / "workspaces" / "myproj"
        workspace.mkdir(parents=True)

        args = argparse.Namespace(project=str(workspace), entrypoint=None)

        with start_mocks() as m:
            proj = self._make_workset_proj(ws_root, "myproj")
            m.resolve_any_project.return_value = proj

            rc = run_shell(args)

        assert rc == 0
        m.resolve_any_project.assert_called()  # explicit-create gate probes existence, then resolves

    def test_resume_works_with_workset(self, start_mocks, tmp_path):
        """start -R auto-detects workset mode via resolve_any_project."""
        from kanibako.commands.start import _run_container

        ws_root = tmp_path / "my-workset"
        ws_root.mkdir()
        workspace = ws_root / "workspaces" / "myproj"
        workspace.mkdir(parents=True)

        with start_mocks() as m:
            proj = self._make_workset_proj(ws_root, "myproj")
            m.resolve_any_project.return_value = proj

            rc = _run_container(
                project_dir=str(workspace), entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=True,
                extra_args=[],
            )

        assert rc == 0
        m.resolve_any_project.assert_called()  # explicit-create gate probes existence, then resolves

    # ``test_start_workset_no_orphan_hint`` removed with the launch-path orphan
    # hint itself (MBR-6 residual 2) — see the note in
    # ``tests/test_commands/test_start_extended.py``.

"""Tests for kanibako.cli."""

from __future__ import annotations

import logging

import pytest

from kanibako.cli import build_parser


class TestParser:
    def test_version(self, capsys):
        from kanibako import __version__
        from kanibako.cli import main

        with pytest.raises(SystemExit) as exc_info:
            main(["--version"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert __version__ in captured.out

    def test_start_default(self):
        parser = build_parser()
        args = parser.parse_args(["start"])
        assert args.command == "start"

    def test_start_with_flags(self):
        parser = build_parser()
        args = parser.parse_args(["start", "-N", "-S", "--image", "my-image:v1"])
        assert args.new_session is True
        assert args.secure is True
        assert args.image == "my-image:v1"

    def test_start_resume_flag(self):
        parser = build_parser()
        args = parser.parse_args(["start", "-R"])
        assert args.resume_session is True

    def test_start_model_flag(self):
        parser = build_parser()
        args = parser.parse_args(["start", "-M", "opus"])
        assert args.model == "opus"

    def test_start_autonomous_flag(self):
        parser = build_parser()
        args = parser.parse_args(["start", "-A"])
        assert args.autonomous is True

    def test_start_env_flag(self):
        parser = build_parser()
        args = parser.parse_args(["start", "-e", "FOO=bar", "-e", "BAZ=qux"])
        assert args.env == ["FOO=bar", "BAZ=qux"]

    def test_start_persistent_flag(self):
        parser = build_parser()
        args = parser.parse_args(["start", "--persistent"])
        assert args.persistent is True

    def test_start_ephemeral_flag(self):
        parser = build_parser()
        args = parser.parse_args(["start", "--ephemeral"])
        assert args.ephemeral is True

    def test_start_entrypoint_flag(self):
        parser = build_parser()
        args = parser.parse_args(["start", "--entrypoint", "/bin/zsh"])
        assert args.entrypoint == "/bin/zsh"

    def test_start_project_positional(self):
        """Project positional is extracted from agent_args in run_start."""
        # Verify that when parsing 'start /tmp/myproject', the path
        # ends up in agent_args (since argparse REMAINDER catches it),
        # and run_start extracts it as the project directory.
        parser = build_parser()
        args = parser.parse_args(["start", "/tmp/myproject"])
        # REMAINDER captures the project path
        assert args.agent_args == ["/tmp/myproject"]

    def test_shell_command(self):
        parser = build_parser()
        args = parser.parse_args(["shell"])
        assert args.command == "shell"

    def test_box_command(self):
        parser = build_parser()
        args = parser.parse_args(["box"])
        assert args.command == "box"

    def test_box_list(self):
        parser = build_parser()
        args = parser.parse_args(["box", "list"])
        assert args.command == "box"
        assert args.box_command == "list"

    def test_box_list_active(self):
        parser = build_parser()
        args = parser.parse_args(["box", "list", "--active"])
        assert args.command == "box"
        assert args.box_command == "list"
        assert args.active is True

    def test_box_archive_command(self):
        parser = build_parser()
        args = parser.parse_args(["box", "archive", "/tmp/project", "out.txz"])
        assert args.command == "box"
        assert args.box_command == "archive"
        assert args.path == "/tmp/project"
        assert args.file == "out.txz"

    def test_box_archive_flags(self):
        parser = build_parser()
        args = parser.parse_args(
            ["box", "archive", "/tmp/proj", "--allow-uncommitted", "--allow-unpushed"]
        )
        assert args.allow_uncommitted is True
        assert args.allow_unpushed is True

    def test_box_archive_all(self):
        parser = build_parser()
        args = parser.parse_args(["box", "archive", "--all"])
        assert args.all_projects is True
        assert args.path is None

    def test_box_purge_command(self):
        parser = build_parser()
        args = parser.parse_args(["box", "purge", "/tmp/project", "--force"])
        assert args.command == "box"
        assert args.box_command == "purge"
        assert args.force is True

    def test_box_purge_all(self):
        parser = build_parser()
        args = parser.parse_args(["box", "purge", "--all"])
        assert args.all_projects is True
        assert args.path is None

    def test_box_extract_command(self):
        parser = build_parser()
        args = parser.parse_args(["box", "extract", "archive.txz", "/tmp/project"])
        assert args.command == "box"
        assert args.box_command == "extract"
        assert args.file == "archive.txz"
        assert args.path == "/tmp/project"

    def test_box_extract_all(self):
        parser = build_parser()
        args = parser.parse_args(["box", "extract", "--all"])
        assert args.all_archives is True
        assert args.file is None

    def test_box_move_command(self):
        parser = build_parser()
        args = parser.parse_args(["box", "move", "/src", "/dest"])
        assert args.command == "box"
        assert args.box_command == "move"
        assert args.args == ["/src", "/dest"]

    def test_box_move_single_arg(self):
        parser = build_parser()
        args = parser.parse_args(["box", "move", "/dest"])
        assert args.args == ["/dest"]

    def test_box_move_force(self):
        parser = build_parser()
        args = parser.parse_args(["box", "move", "/dest", "--force"])
        assert args.force is True

    def test_box_vault_list(self):
        parser = build_parser()
        args = parser.parse_args(["box", "vault", "list"])
        assert args.command == "box"
        assert args.vault_command == "list"

    def test_box_vault_snapshot(self):
        parser = build_parser()
        args = parser.parse_args(["box", "vault", "snapshot", "/myproj"])
        assert args.vault_command == "snapshot"
        assert args.project == "/myproj"

    def test_box_vault_restore(self):
        parser = build_parser()
        args = parser.parse_args(["box", "vault", "restore", "snap.tar.xz"])
        assert args.vault_command == "restore"
        assert args.name == "snap.tar.xz"

    def test_box_vault_prune(self):
        parser = build_parser()
        args = parser.parse_args(["box", "vault", "prune", "--keep", "3"])
        assert args.vault_command == "prune"
        assert args.keep == 3

    def test_box_vault_list_quiet(self):
        parser = build_parser()
        args = parser.parse_args(["box", "vault", "list", "-q"])
        assert args.quiet is True

    def test_box_migrate_command(self):
        parser = build_parser()
        args = parser.parse_args(["box", "migrate", "/old", "/new"])
        assert args.command == "box"
        assert args.box_command == "migrate"
        assert args.old_path == "/old"
        assert args.new_path == "/new"

    def test_box_migrate_defaults_new_path(self):
        parser = build_parser()
        args = parser.parse_args(["box", "migrate", "/old"])
        assert args.old_path == "/old"
        assert args.new_path is None

    def test_box_migrate_force(self):
        parser = build_parser()
        args = parser.parse_args(["box", "migrate", "/old", "--force"])
        assert args.force is True

    def test_box_duplicate_command(self):
        parser = build_parser()
        args = parser.parse_args(["box", "duplicate", "/src", "/dst"])
        assert args.command == "box"
        assert args.box_command == "duplicate"
        assert args.source_path == "/src"
        assert args.new_path == "/dst"
        assert args.bare is False
        assert args.force is False

    def test_box_duplicate_bare(self):
        parser = build_parser()
        args = parser.parse_args(["box", "duplicate", "/src", "/dst", "--bare"])
        assert args.bare is True

    def test_box_duplicate_force(self):
        parser = build_parser()
        args = parser.parse_args(["box", "duplicate", "/src", "/dst", "--force"])
        assert args.force is True

    def test_box_duplicate_bare_and_force(self):
        parser = build_parser()
        args = parser.parse_args(["box", "duplicate", "/src", "/dst", "--bare", "--force"])
        assert args.bare is True
        assert args.force is True

    def test_image_command(self):
        parser = build_parser()
        args = parser.parse_args(["image"])
        assert args.command == "image"

    def test_image_list(self):
        parser = build_parser()
        args = parser.parse_args(["image", "list"])
        assert args.command == "image"
        assert args.image_command == "list"

    def test_image_rebuild(self):
        parser = build_parser()
        args = parser.parse_args(["image", "rebuild"])
        assert args.command == "image"
        assert args.image_command == "rebuild"
        assert args.image is None
        assert args.all_images is False

    def test_image_rebuild_specific(self):
        parser = build_parser()
        args = parser.parse_args(["image", "rebuild", "kanibako-oci:latest"])
        assert args.image == "kanibako-oci:latest"

    def test_image_rebuild_all(self):
        parser = build_parser()
        args = parser.parse_args(["image", "rebuild", "--all"])
        assert args.all_images is True

    def test_system_command(self):
        parser = build_parser()
        args = parser.parse_args(["system"])
        assert args.command == "system"

    def test_system_info(self):
        parser = build_parser()
        args = parser.parse_args(["system", "info"])
        assert args.command == "system"
        assert args.system_command == "info"

    def test_system_info_alias_inspect(self):
        parser = build_parser()
        args = parser.parse_args(["system", "inspect"])
        assert args.command == "system"
        assert hasattr(args, "func")

    def test_system_config(self):
        parser = build_parser()
        args = parser.parse_args(["system", "config"])
        assert args.command == "system"
        assert args.system_command == "config"

    def test_system_config_set(self):
        parser = build_parser()
        args = parser.parse_args(["system", "config", "image=custom:v1"])
        assert args.key_value == "image=custom:v1"

    def test_system_config_get(self):
        parser = build_parser()
        args = parser.parse_args(["system", "config", "image"])
        assert args.key_value == "image"

    def test_system_config_reset(self):
        parser = build_parser()
        args = parser.parse_args(["system", "config", "--reset", "image"])
        assert args.reset is True
        assert args.key_value == "image"

    def test_system_config_reset_all(self):
        parser = build_parser()
        args = parser.parse_args(["system", "config", "--reset", "--all"])
        assert args.reset is True
        assert args.all_keys is True

    def test_system_config_effective(self):
        parser = build_parser()
        args = parser.parse_args(["system", "config", "--effective"])
        assert args.effective is True

    def test_system_upgrade(self):
        parser = build_parser()
        args = parser.parse_args(["system", "upgrade"])
        assert args.command == "system"
        assert args.system_command == "upgrade"
        assert args.check is False

    def test_system_upgrade_check(self):
        parser = build_parser()
        args = parser.parse_args(["system", "upgrade", "--check"])
        assert args.command == "system"
        assert args.check is True

    def test_agent_command(self):
        parser = build_parser()
        args = parser.parse_args(["agent"])
        assert args.command == "agent"

    def test_agent_list(self):
        parser = build_parser()
        args = parser.parse_args(["agent", "list"])
        assert args.command == "agent"
        assert args.agent_command == "list"

    def test_agent_list_quiet(self):
        parser = build_parser()
        args = parser.parse_args(["agent", "list", "-q"])
        assert args.quiet is True

    def test_agent_list_alias_ls(self):
        parser = build_parser()
        args = parser.parse_args(["agent", "ls"])
        assert args.command == "agent"
        assert hasattr(args, "func")

    def test_agent_info(self):
        parser = build_parser()
        args = parser.parse_args(["agent", "info", "myagent"])
        assert args.command == "agent"
        assert args.agent_command == "info"
        assert args.agent_id == "myagent"

    def test_agent_info_alias_inspect(self):
        parser = build_parser()
        args = parser.parse_args(["agent", "inspect", "myagent"])
        assert args.command == "agent"
        assert args.agent_id == "myagent"

    def test_agent_config_show(self):
        parser = build_parser()
        args = parser.parse_args(["agent", "config", "myagent"])
        assert args.command == "agent"
        assert args.agent_command == "config"
        assert args.agent_id == "myagent"
        assert args.key_value is None

    def test_agent_config_set(self):
        parser = build_parser()
        args = parser.parse_args(["agent", "config", "myagent", "model=sonnet"])
        assert args.agent_id == "myagent"
        assert args.key_value == "model=sonnet"

    def test_agent_config_get(self):
        parser = build_parser()
        args = parser.parse_args(["agent", "config", "myagent", "model"])
        assert args.key_value == "model"

    def test_agent_config_reset(self):
        parser = build_parser()
        args = parser.parse_args(["agent", "config", "myagent", "--reset", "model"])
        assert args.reset == "model"

    def test_agent_config_reset_all(self):
        parser = build_parser()
        args = parser.parse_args(["agent", "config", "myagent", "--reset", "--all"])
        assert args.reset == "__RESET__"
        assert args.all_keys is True

    def test_agent_reauth(self):
        parser = build_parser()
        args = parser.parse_args(["agent", "reauth"])
        assert args.command == "agent"
        assert args.agent_command == "reauth"
        assert args.project is None

    def test_agent_reauth_with_project(self):
        parser = build_parser()
        args = parser.parse_args(["agent", "reauth", "/tmp/myproj"])
        assert args.agent_command == "reauth"
        assert args.project == "/tmp/myproj"

    def test_agent_helper_spawn(self):
        parser = build_parser()
        args = parser.parse_args(["agent", "helper", "spawn", "--depth", "3"])
        assert args.command == "agent"
        assert args.agent_command == "helper"
        assert args.helper_command == "spawn"
        assert args.depth == 3

    def test_agent_helper_list(self):
        parser = build_parser()
        args = parser.parse_args(["agent", "helper", "list"])
        assert args.command == "agent"
        assert args.helper_command == "list"

    def test_agent_fork(self):
        parser = build_parser()
        args = parser.parse_args(["agent", "fork", "feature1"])
        assert args.command == "agent"
        assert args.agent_command == "fork"
        assert args.name == "feature1"

    def test_stop_command(self):
        parser = build_parser()
        args = parser.parse_args(["stop"])
        assert args.command == "stop"
        assert args.project is None
        assert args.all_containers is False
        assert args.force is False

    def test_stop_with_path(self):
        parser = build_parser()
        args = parser.parse_args(["stop", "/tmp/myproject"])
        assert args.command == "stop"
        assert args.project == "/tmp/myproject"

    def test_stop_all(self):
        parser = build_parser()
        args = parser.parse_args(["stop", "--all"])
        assert args.command == "stop"
        assert args.all_containers is True

    def test_start_with_agent_args(self):
        parser = build_parser()
        args = parser.parse_args(["start", "--", "--some-flag", "arg"])
        assert args.agent_args == ["--", "--some-flag", "arg"]

    def test_box_start(self):
        parser = build_parser()
        args = parser.parse_args(["box", "start"])
        assert args.command == "box"
        assert args.box_command == "start"

    def test_box_start_with_flags(self):
        parser = build_parser()
        args = parser.parse_args(["box", "start", "-N", "-A", "-M", "opus"])
        assert args.new_session is True
        assert args.autonomous is True
        assert args.model == "opus"

    def test_box_info(self):
        parser = build_parser()
        args = parser.parse_args(["box", "info"])
        assert args.command == "box"
        assert args.box_command == "info"
        assert args.path is None

    def test_box_info_with_path(self):
        parser = build_parser()
        args = parser.parse_args(["box", "info", "/tmp/myproject"])
        assert args.box_command == "info"
        assert args.path == "/tmp/myproject"

    def test_workset_command(self):
        parser = build_parser()
        args = parser.parse_args(["workset"])
        assert args.command == "workset"

    def test_workset_create(self):
        parser = build_parser()
        args = parser.parse_args(["workset", "create", "/tmp/ws", "--name", "myws"])
        assert args.command == "workset"
        assert args.workset_command == "create"
        assert args.name == "myws"
        assert args.path == "/tmp/ws"

    def test_workset_create_path_only(self):
        parser = build_parser()
        args = parser.parse_args(["workset", "create", "/tmp/ws"])
        assert args.command == "workset"
        assert args.workset_command == "create"
        assert args.path == "/tmp/ws"
        assert args.name is None

    def test_workset_list(self):
        parser = build_parser()
        args = parser.parse_args(["workset", "list"])
        assert args.command == "workset"
        assert args.workset_command == "list"

    def test_workset_list_quiet(self):
        parser = build_parser()
        args = parser.parse_args(["workset", "list", "-q"])
        assert args.quiet is True

    def test_workset_list_alias_ls(self):
        parser = build_parser()
        args = parser.parse_args(["workset", "ls"])
        assert args.command == "workset"
        assert hasattr(args, "func")

    def test_workset_rm(self):
        parser = build_parser()
        args = parser.parse_args(["workset", "rm", "myws", "--purge", "--force"])
        assert args.command == "workset"
        assert args.workset_command in ("rm", "delete")
        assert args.name == "myws"
        assert args.purge is True
        assert args.force is True

    def test_workset_rm_alias_delete(self):
        parser = build_parser()
        args = parser.parse_args(["workset", "delete", "myws", "--force"])
        assert args.command == "workset"
        assert args.name == "myws"
        assert args.force is True

    def test_workset_connect(self):
        parser = build_parser()
        args = parser.parse_args(["workset", "connect", "myws", "/tmp/src", "--name", "proj"])
        assert args.command == "workset"
        assert args.workset_command == "connect"
        assert args.workset == "myws"
        assert args.source == "/tmp/src"
        assert args.project_name == "proj"

    def test_workset_disconnect(self):
        parser = build_parser()
        args = parser.parse_args(["workset", "disconnect", "myws", "proj", "--remove-files", "--force"])
        assert args.command == "workset"
        assert args.workset_command == "disconnect"
        assert args.workset == "myws"
        assert args.project == "proj"
        assert args.remove_files is True
        assert args.force is True

    def test_workset_info(self):
        parser = build_parser()
        args = parser.parse_args(["workset", "info", "myws"])
        assert args.command == "workset"
        assert args.workset_command in ("info", "inspect")
        assert args.name == "myws"

    def test_workset_info_alias_inspect(self):
        parser = build_parser()
        args = parser.parse_args(["workset", "inspect", "myws"])
        assert args.command == "workset"
        assert args.name == "myws"

    def test_workset_config(self):
        parser = build_parser()
        args = parser.parse_args(["workset", "config", "myws", "model=sonnet"])
        assert args.command == "workset"
        assert args.workset_command == "config"
        assert args.workset == "myws"
        assert args.key_value == "model=sonnet"

    def test_box_migrate_workset_flag(self):
        parser = build_parser()
        args = parser.parse_args(["box", "migrate", "--to", "workset", "--workset", "myws", "--name", "proj"])
        assert args.to_mode == "workset"
        assert args.workset == "myws"
        assert args.project_name == "proj"

    def test_box_migrate_in_place_flag(self):
        parser = build_parser()
        args = parser.parse_args(["box", "migrate", "--to", "workset", "--workset", "myws", "--in-place"])
        assert args.in_place is True

    def test_box_duplicate_workset_flag(self):
        parser = build_parser()
        args = parser.parse_args(["box", "duplicate", "/src", "/dst", "--to", "workset", "--workset", "myws", "--name", "proj"])
        assert args.to_mode == "workset"
        assert args.workset == "myws"
        assert args.project_name == "proj"

    # -- Top-level alias tests --

    def test_list_top_level(self):
        parser = build_parser()
        args = parser.parse_args(["list"])
        assert args.command == "list"
        assert hasattr(args, "func")

    def test_list_top_level_active(self):
        parser = build_parser()
        args = parser.parse_args(["list", "--active"])
        assert args.command == "list"
        assert args.active is True

    def test_list_top_level_all(self):
        parser = build_parser()
        args = parser.parse_args(["list", "--all"])
        assert args.command == "list"
        assert args.show_all is True

    def test_list_top_level_quiet(self):
        parser = build_parser()
        args = parser.parse_args(["list", "-q"])
        assert args.command == "list"
        assert args.quiet is True

    def test_list_in_subcommands(self):
        from kanibako.cli import _SUBCOMMANDS
        assert "list" in _SUBCOMMANDS

    def test_ps_top_level(self):
        parser = build_parser()
        args = parser.parse_args(["ps"])
        assert args.command == "ps"
        assert hasattr(args, "func")

    def test_ps_top_level_all(self):
        parser = build_parser()
        args = parser.parse_args(["ps", "--all"])
        assert args.command == "ps"
        assert args.show_all is True

    def test_ps_top_level_quiet(self):
        parser = build_parser()
        args = parser.parse_args(["ps", "-q"])
        assert args.command == "ps"
        assert args.quiet is True

    def test_create_top_level(self):
        parser = build_parser()
        args = parser.parse_args(["create", "/tmp/proj"])
        assert args.command == "create"
        assert args.path == "/tmp/proj"
        assert hasattr(args, "func")

    def test_create_top_level_standalone(self):
        parser = build_parser()
        args = parser.parse_args(["create", "/tmp/proj", "--standalone"])
        assert args.command == "create"
        assert args.standalone is True

    def test_create_top_level_with_image(self):
        parser = build_parser()
        args = parser.parse_args(["create", "-i", "myimage:v1"])
        assert args.command == "create"
        assert args.image == "myimage:v1"

    def test_create_top_level_no_path(self):
        parser = build_parser()
        args = parser.parse_args(["create"])
        assert args.command == "create"
        assert args.path is None

    def test_rm_top_level(self):
        parser = build_parser()
        args = parser.parse_args(["rm", "myproj"])
        assert args.command == "rm"
        assert args.target == "myproj"
        assert hasattr(args, "func")

    def test_rm_top_level_purge(self):
        parser = build_parser()
        args = parser.parse_args(["rm", "myproj", "--purge"])
        assert args.command == "rm"
        assert args.purge is True

    def test_rm_top_level_force(self):
        parser = build_parser()
        args = parser.parse_args(["rm", "myproj", "--purge", "--force"])
        assert args.command == "rm"
        assert args.purge is True
        assert args.force is True

    def test_subcommands_set(self):
        from kanibako.cli import _SUBCOMMANDS
        expected = {
            # Top-level aliases
            "start", "stop", "shell", "ps", "list", "create", "rm",
            # Management commands
            "box", "image", "workset", "agent", "system",
            # Command aliases (#62)
            "crab", "rig", "container",
        }
        assert _SUBCOMMANDS == expected

    def test_crab_alias_in_subcommands(self):
        from kanibako.cli import _SUBCOMMANDS
        assert "crab" in _SUBCOMMANDS

    def test_rig_alias_in_subcommands(self):
        from kanibako.cli import _SUBCOMMANDS
        assert "rig" in _SUBCOMMANDS

    def test_container_alias_in_subcommands(self):
        from kanibako.cli import _SUBCOMMANDS
        assert "container" in _SUBCOMMANDS

    def test_command_aliases_mapping(self):
        from kanibako.cli import _COMMAND_ALIASES
        assert _COMMAND_ALIASES == {
            "crab": "agent",
            "rig": "image",
            "container": "box",
        }


class TestLazyInitExemptions:
    """Commands that skip lazy initialization."""

    def test_agent_helper_skips_lazy_init(self, tmp_path, monkeypatch):
        """'agent helper' command should not trigger lazy init."""
        # Point XDG_CONFIG_HOME to an empty dir (no kanibako.toml)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr(
            "kanibako.commands.helper_cmd._helpers_dir",
            lambda: tmp_path / "helpers",
        )

        from kanibako.cli import main
        # 'agent helper list' should not crash with "not set up yet"
        with pytest.raises(SystemExit) as exc_info:
            main(["agent", "helper", "list"])
        assert exc_info.value.code == 0

    def test_agent_fork_skips_lazy_init(self, tmp_path, monkeypatch):
        """'agent fork' command should not trigger lazy init."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        # fork will fail with "no socket" but should NOT fail with lazy init
        from kanibako.cli import main
        with pytest.raises(SystemExit) as exc_info:
            main(["agent", "fork", "test"])
        # Should exit with 1 (no socket), not a lazy init error
        assert exc_info.value.code == 1

    def test_system_triggers_lazy_init(self, tmp_path, monkeypatch):
        """'system' command triggers lazy init (creates config)."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        (tmp_path / "home").mkdir(parents=True, exist_ok=True)

        from kanibako.cli import main
        with pytest.raises(SystemExit) as exc_info:
            main(["system", "info"])
        assert exc_info.value.code == 0
        # Config should have been created by lazy init
        assert (tmp_path / "config" / "kanibako.toml").exists()


class TestVerboseFlag:
    def test_verbose_short_sets_debug(self):
        from kanibako.cli import main

        with pytest.raises(SystemExit):
            main(["-v", "--version"])
        logger = logging.getLogger("kanibako")
        assert logger.level == logging.DEBUG

    def test_verbose_long_sets_debug(self):
        from kanibako.cli import main

        with pytest.raises(SystemExit):
            main(["--verbose", "--version"])
        logger = logging.getLogger("kanibako")
        assert logger.level == logging.DEBUG

    def test_no_verbose_sets_warning(self):
        from kanibako.cli import main

        with pytest.raises(SystemExit):
            main(["--version"])
        logger = logging.getLogger("kanibako")
        assert logger.level == logging.WARNING

    def test_verbose_stripped_from_args(self):
        """Verbose flag should not reach subcommand parsing."""
        from kanibako.cli import main

        # -v before --help should not error out
        with pytest.raises(SystemExit) as exc_info:
            main(["-v", "--help"])
        assert exc_info.value.code == 0

    def test_epilog_mentions_verbose(self):
        parser = build_parser()
        assert "-v, --verbose" in parser.epilog

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

    def test_setup_command(self):
        parser = build_parser()
        args = parser.parse_args(["setup"])
        assert args.command == "setup"

    def test_remove_command(self):
        parser = build_parser()
        args = parser.parse_args(["remove"])
        assert args.command == "remove"

    def test_reauth_command(self):
        parser = build_parser()
        args = parser.parse_args(["reauth"])
        assert args.command == "reauth"

    def test_upgrade_command(self):
        parser = build_parser()
        args = parser.parse_args(["upgrade"])
        assert args.command == "upgrade"
        assert args.check is False

    def test_upgrade_check(self):
        parser = build_parser()
        args = parser.parse_args(["upgrade", "--check"])
        assert args.command == "upgrade"
        assert args.check is True

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


class TestConfigCheckExemptions:
    """Commands that skip the kanibako.toml existence check."""

    def test_helper_skips_config_check(self, tmp_path, monkeypatch):
        """'helper' command should not require kanibako.toml."""
        # Point XDG_CONFIG_HOME to an empty dir (no kanibako.toml)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr(
            "kanibako.commands.helper_cmd._helpers_dir",
            lambda: tmp_path / "helpers",
        )

        from kanibako.cli import main
        # 'helper list' should not crash with "not set up yet"
        with pytest.raises(SystemExit) as exc_info:
            main(["helper", "list"])
        assert exc_info.value.code == 0

    def test_setup_skips_config_check(self):
        """'setup' command should not require kanibako.toml (pre-existing)."""
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["setup"])
        assert args.command == "setup"


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

"""Tests for kanibako.cli."""

from __future__ import annotations

import logging

import pytest

from kanibako.cli import build_parser


def _run_main_capturing(cmd: str, extra: list[str], *, target: str = "start") -> dict:
    """Run cli.main and capture the args namespace seen by the dispatcher.

    Patches the dispatcher (run_start or run_shell) so we can inspect what
    main() produced — including the '--' split and post-parse args.agent_args
    or args.shell_args injection.
    """
    from unittest.mock import patch
    from kanibako.cli import main as cli_main

    captured: dict = {}

    def fake_func(args):
        for name in (
            "project", "agent_args", "shell_args", "env", "ephemeral",
            "persistent", "entrypoint",
        ):
            captured[name] = getattr(args, name, None)
        return 0

    target_path = f"kanibako.commands.start.run_{target}"
    with patch(target_path, fake_func), \
         patch("kanibako.cli._ensure_initialized"), \
         patch("sys.exit"):
        cli_main([cmd] + extra)
    return captured


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

    def test_start_rig_synonym(self):
        parser = build_parser()
        args = parser.parse_args(["start", "x", "--rig", "jvm"])
        assert args.image == "jvm"

    def test_start_image_still_works(self):
        parser = build_parser()
        args = parser.parse_args(["start", "x", "--image", "jvm"])
        assert args.image == "jvm"

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

    def test_start_detach_tristate(self):
        """E2h regression lock: --detach/--attach is a store_const tri-state.
        Byte-safe with the pre-change store_true/store_false: unset -> None (the
        attach default), --detach/--background -> True, --attach -> False (still
        'do not detach').  run_start normalises None/False -> no detach and uses
        the explicit False only to reject a contradictory --warm-only --attach."""
        parser = build_parser()
        assert parser.parse_args(["start"]).detach is None
        assert parser.parse_args(["start", "--detach"]).detach is True
        assert parser.parse_args(["start", "--background"]).detach is True
        assert parser.parse_args(["start", "--attach"]).detach is False

    def test_start_warm_only_flag(self):
        """E2h: --warm-only is a plain store_true (default False), independent of
        the detach tri-state (the semantic conflict is enforced in run_start)."""
        parser = build_parser()
        assert parser.parse_args(["start"]).warm_only is False
        args = parser.parse_args(["start", "--warm-only"])
        assert args.warm_only is True
        assert args.detach is None  # --warm-only does not touch the detach flag

    def test_start_detach_attach_mutually_exclusive(self):
        """--detach and --attach remain mutually exclusive at the parser."""
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["start", "--detach", "--attach"])

    def test_start_entrypoint_flag(self):
        parser = build_parser()
        args = parser.parse_args(["start", "--entrypoint", "/bin/zsh"])
        assert args.entrypoint == "/bin/zsh"

    def test_start_project_positional(self):
        """Project positional is bound to args.project directly."""
        parser = build_parser()
        args = parser.parse_args(["start", "/tmp/myproject"])
        assert args.project == "/tmp/myproject"

    def test_shell_command(self):
        parser = build_parser()
        args = parser.parse_args(["shell"])
        assert args.command == "shell"

    def test_shell_rig_synonym(self):
        parser = build_parser()
        args = parser.parse_args(["shell", "x", "--rig", "jvm"])
        assert args.image == "jvm"

    def test_shell_image_still_works(self):
        parser = build_parser()
        args = parser.parse_args(["shell", "x", "--image", "jvm"])
        assert args.image == "jvm"

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
        assert args.old == "/src"
        assert args.new == "/dest"

    def test_box_move_alias_mv(self):
        parser = build_parser()
        args = parser.parse_args(["box", "mv", "/src", "/dest"])
        assert args.box_command == "mv"
        assert args.old == "/src"
        assert args.new == "/dest"

    def test_box_move_requires_both_paths(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["box", "move", "/dest"])

    def test_box_move_force(self):
        parser = build_parser()
        args = parser.parse_args(["box", "move", "/src", "/dest", "--force"])
        assert args.force is True

    def test_box_move_workset_target(self):
        parser = build_parser()
        args = parser.parse_args(["box", "move", "/src", "/dest", "--workset", "ws"])
        assert args.to_workset == "ws"

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

    def test_box_remap_command(self):
        parser = build_parser()
        args = parser.parse_args(["box", "remap", "/old", "/new"])
        assert args.command == "box"
        assert args.box_command == "remap"
        assert args.old == "/old"
        assert args.new == "/new"

    def test_box_remap_defaults_new(self):
        parser = build_parser()
        args = parser.parse_args(["box", "remap", "/old"])
        assert args.old == "/old"
        assert args.new is None

    def test_box_remap_force(self):
        parser = build_parser()
        args = parser.parse_args(["box", "remap", "/old", "--force"])
        assert args.force is True

    def test_box_migrate_is_gone(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["box", "migrate", "/old", "/new"])

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

    def test_rig_command(self):
        parser = build_parser()
        args = parser.parse_args(["rig"])
        assert args.command == "rig"

    def test_rig_list(self):
        parser = build_parser()
        args = parser.parse_args(["rig", "list"])
        assert args.command == "rig"
        assert args.rig_command == "list"

    def test_rig_prep(self):
        parser = build_parser()
        args = parser.parse_args(["rig", "prep"])
        assert args.command == "rig"
        assert args.rig_command == "prep"

    def test_rig_prep_force_all(self):
        parser = build_parser()
        args = parser.parse_args(["rig", "prep", "--all", "--force"])
        assert args.all_images is True
        assert args.force is True

    def test_rig_rebuild_removed(self):
        """W2a: the deprecated 'rig rebuild' shim was removed."""
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["rig", "rebuild"])

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

    def test_system_show(self):
        parser = build_parser()
        args = parser.parse_args(["system", "show"])
        assert args.command == "system"
        assert args.system_command == "show"
        assert args.func.__name__ == "run_show"

    def test_system_set(self):
        parser = build_parser()
        args = parser.parse_args(["system", "set", "image=custom:v1"])
        assert args.key_value == "image=custom:v1"
        assert args.func.__name__ == "run_set"

    def test_system_get(self):
        parser = build_parser()
        args = parser.parse_args(["system", "get", "image"])
        assert args.key == "image"
        assert args.func.__name__ == "run_get"

    def test_system_reset(self):
        parser = build_parser()
        args = parser.parse_args(["system", "reset", "image"])
        assert args.key == "image"
        assert args.all_keys is False
        assert args.func.__name__ == "run_reset"

    def test_system_reset_all(self):
        parser = build_parser()
        args = parser.parse_args(["system", "reset", "--all"])
        assert args.all_keys is True

    def test_system_show_effective(self):
        parser = build_parser()
        args = parser.parse_args(["system", "show", "--effective"])
        assert args.effective is True

    def test_system_config_subcommand_retired(self):
        import pytest
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["system", "config"])

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

    def test_agent_show(self):
        parser = build_parser()
        args = parser.parse_args(["agent", "show", "myagent"])
        assert args.command == "agent"
        assert args.agent_command == "show"
        assert args.agent_id == "myagent"
        assert args.func.__name__ == "run_show"

    def test_agent_set(self):
        parser = build_parser()
        args = parser.parse_args(["agent", "set", "myagent", "model=sonnet"])
        assert args.agent_id == "myagent"
        assert args.key_value == "model=sonnet"
        assert args.func.__name__ == "run_set"

    def test_agent_get(self):
        parser = build_parser()
        args = parser.parse_args(["agent", "get", "myagent", "model"])
        assert args.key == "model"
        assert args.func.__name__ == "run_get"

    def test_agent_reset(self):
        parser = build_parser()
        args = parser.parse_args(["agent", "reset", "myagent", "model"])
        assert args.key == "model"
        assert args.func.__name__ == "run_reset"

    def test_agent_reset_all(self):
        parser = build_parser()
        args = parser.parse_args(["agent", "reset", "myagent", "--all"])
        assert args.all_keys is True

    def test_agent_config_subcommand_retired(self):
        import pytest
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["agent", "config", "myagent"])

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

    def test_box_helper_spawn(self):
        parser = build_parser()
        args = parser.parse_args(["box", "helper", "spawn", "--depth", "3"])
        assert args.command == "box"
        assert args.box_command == "helper"
        assert args.helper_command == "spawn"
        assert args.depth == 3

    def test_box_helper_list(self):
        parser = build_parser()
        args = parser.parse_args(["box", "helper", "list"])
        assert args.command == "box"
        assert args.helper_command == "list"

    def test_box_fork(self):
        parser = build_parser()
        args = parser.parse_args(["box", "fork", "feature1"])
        assert args.command == "box"
        assert args.box_command == "fork"
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
        """Args after '--' are routed to args.agent_args by main().

        The parser itself doesn't see them — main() splits at '--' and
        sets args.agent_args directly. We test the end-to-end behavior
        via main() since the parser alone won't bind agent_args.
        """
        captured = _run_main_capturing("start", ["--", "--some-flag", "arg"])
        assert captured["agent_args"] == ["--some-flag", "arg"]
        assert captured["project"] is None

    def test_start_flags_after_positional(self):
        """Flags following the project positional are still consumed by kanibako."""
        captured = _run_main_capturing(
            "start",
            ["myproj", "--ephemeral", "-e", "FOO=1", "-e", "BAR=2"],
        )
        assert captured["project"] == "myproj"
        assert captured["ephemeral"] is True
        assert captured["env"] == ["FOO=1", "BAR=2"]
        assert captured["agent_args"] == []

    def test_start_flags_before_positional(self):
        """Flags before the project positional also work (regression check)."""
        captured = _run_main_capturing(
            "start",
            ["--ephemeral", "-e", "FOO=1", "myproj"],
        )
        assert captured["project"] == "myproj"
        assert captured["ephemeral"] is True
        assert captured["env"] == ["FOO=1"]

    def test_start_positional_then_dash_dash(self):
        """Positional + '--' + agent args."""
        captured = _run_main_capturing(
            "start",
            ["myproj", "--ephemeral", "--", "--continue", "extra"],
        )
        assert captured["project"] == "myproj"
        assert captured["ephemeral"] is True
        assert captured["agent_args"] == ["--continue", "extra"]

    def test_shell_flags_after_positional(self):
        """Shell command: flags after positional work."""
        captured = _run_main_capturing(
            "shell",
            ["myproj", "--ephemeral", "--entrypoint", "/bin/sh", "--", "-c", "echo hi"],
            target="shell",
        )
        assert captured["project"] == "myproj"
        assert captured["ephemeral"] is True
        assert captured["entrypoint"] == "/bin/sh"
        assert captured["shell_args"] == ["-c", "echo hi"]

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

    def test_workset_create_force(self):
        parser = build_parser()
        args = parser.parse_args(
            ["workset", "create", "/tmp/ws", "--name", "myws", "--force"]
        )
        assert args.force is True
        args = parser.parse_args(["workset", "create", "/tmp/ws"])
        assert args.force is False

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

    def test_workset_set(self):
        parser = build_parser()
        args = parser.parse_args(["workset", "set", "myws", "model=sonnet"])
        assert args.command == "workset"
        assert args.workset_command == "set"
        assert args.workset == "myws"
        assert args.key_value == "model=sonnet"
        assert args.func.__name__ == "run_set"

    def test_workset_show(self):
        parser = build_parser()
        args = parser.parse_args(["workset", "show", "myws", "--effective"])
        assert args.workset == "myws"
        assert args.effective is True
        assert args.func.__name__ == "run_show"

    def test_workset_get(self):
        parser = build_parser()
        args = parser.parse_args(["workset", "get", "myws", "model"])
        assert args.workset == "myws"
        assert args.key == "model"
        assert args.func.__name__ == "run_get"

    def test_workset_reset_all(self):
        parser = build_parser()
        args = parser.parse_args(["workset", "reset", "myws", "--all"])
        assert args.workset == "myws"
        assert args.reset_all is True
        assert args.func.__name__ == "run_reset"

    def test_workset_config_subcommand_retired(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["workset", "config", "myws", "model=sonnet"])

    def test_box_convert_workset_flag(self):
        parser = build_parser()
        args = parser.parse_args(["box", "convert", "--workset", "myws", "--name", "proj"])
        assert args.box_command == "convert"
        assert args.to_workset == "myws"
        assert args.name == "proj"

    def test_box_convert_requires_target(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["box", "convert"])

    def test_box_convert_targets_mutually_exclusive(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["box", "convert", "--default", "--standalone"])

    def test_box_convert_bare_move(self):
        parser = build_parser()
        from kanibako.commands.box._lifecycle import _BARE_MOVE
        args = parser.parse_args(["box", "convert", "--workset", "ws", "--move"])
        assert args.move is _BARE_MOVE

    def test_box_convert_move_path(self):
        parser = build_parser()
        args = parser.parse_args(["box", "convert", "--standalone", "--move", "/dest"])
        assert args.move == "/dest"
        assert args.to_standalone is True

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

    def test_create_top_level_force(self):
        parser = build_parser()
        args = parser.parse_args(["create", "/tmp/proj", "--name", "x", "--force"])
        assert args.force is True
        args = parser.parse_args(["create", "/tmp/proj"])
        assert args.force is False

    def test_box_create_force(self):
        parser = build_parser()
        args = parser.parse_args(["box", "create", "/tmp/proj", "--name", "x", "--force"])
        assert args.force is True
        args = parser.parse_args(["box", "create", "/tmp/proj"])
        assert args.force is False

    def test_create_top_level_with_image(self):
        parser = build_parser()
        args = parser.parse_args(["create", "-i", "myimage:v1"])
        assert args.command == "create"
        assert args.image == "myimage:v1"

    def test_box_create_rig_synonym(self):
        parser = build_parser()
        args = parser.parse_args(["box", "create", "x", "--rig", "X"])
        assert args.image == "X"

    def test_box_create_image_still_works(self):
        parser = build_parser()
        args = parser.parse_args(["box", "create", "x", "--image", "X"])
        assert args.image == "X"

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
            "start", "stop", "shell", "ps", "list", "create", "rm", "register",
            # Management commands
            "box", "rig", "workset", "agent", "system", "baseline",
            # Setup wizard
            "setup",
            # VS Code launcher
            "code",
        }
        assert _SUBCOMMANDS == expected

    def test_agent_in_subcommands(self):
        from kanibako.cli import _SUBCOMMANDS
        assert "agent" in _SUBCOMMANDS

    def test_rig_in_subcommands(self):
        from kanibako.cli import _SUBCOMMANDS
        assert "rig" in _SUBCOMMANDS

    def test_image_container_aliases_removed(self):
        """W2a: the deprecated image→rig / container→box command aliases are gone."""
        from kanibako.cli import _SUBCOMMANDS
        assert "image" not in _SUBCOMMANDS
        assert "container" not in _SUBCOMMANDS
        # The translation table itself was removed entirely.
        import kanibako.cli as cli_mod
        assert not hasattr(cli_mod, "_COMMAND_ALIASES")


class TestNormalizeCommand:
    """Dispatcher reorder: a leading flag must not swallow a later subcommand."""

    def test_leading_agent_flag_before_shell_reorders(self):
        from kanibako.cli import _normalize_command
        # `kanibako --agent goose shell` -> shell leads, flags preserved.
        assert _normalize_command(["--agent", "goose", "shell"]) == [
            "shell", "--agent", "goose",
        ]

    def test_leading_box_flag_before_stop_reorders(self):
        from kanibako.cli import _normalize_command
        assert _normalize_command(["--box", "foo", "stop"]) == [
            "stop", "--box", "foo",
        ]

    def test_bare_positional_unchanged(self):
        from kanibako.cli import _normalize_command
        # `kanibako myproject` -> no subcommand anywhere; caller prepends start.
        assert _normalize_command(["myproject"]) == ["myproject"]

    def test_leading_short_flag_no_subcommand_unchanged(self):
        from kanibako.cli import _normalize_command
        # `kanibako -A` -> leading flag, no subcommand; caller prepends start.
        assert _normalize_command(["-A"]) == ["-A"]
        assert _normalize_command(["-N"]) == ["-N"]

    def test_subcommand_already_leading_unchanged(self):
        from kanibako.cli import _normalize_command
        assert _normalize_command(["shell"]) == ["shell"]
        assert _normalize_command(["start", "x"]) == ["start", "x"]
        assert _normalize_command(["shell", "--agent", "goose"]) == [
            "shell", "--agent", "goose",
        ]

    def test_empty_unchanged(self):
        from kanibako.cli import _normalize_command
        assert _normalize_command([]) == []

    def test_reordered_agent_goose_shell_parses_as_shell(self):
        # End-to-end: after the reorder, build_parser yields command="shell".
        from kanibako.cli import _normalize_command
        parser = build_parser()
        args = parser.parse_args(_normalize_command(["--agent", "goose", "shell"]))
        assert args.command == "shell"
        assert args.agent == "goose"


class TestLazyInitExemptions:
    """Commands that skip lazy initialization."""

    def test_box_helper_skips_lazy_init(self, tmp_path, monkeypatch):
        """'box helper' command should not trigger lazy init."""
        # Point XDG_CONFIG_HOME to an empty dir (no kanibako_config.yaml)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr(
            "kanibako.commands.helper_cmd._helpers_dir",
            lambda: tmp_path / "helpers",
        )

        from kanibako.cli import main
        # 'box helper list' should not crash with "not set up yet"
        with pytest.raises(SystemExit) as exc_info:
            main(["box", "helper", "list"])
        assert exc_info.value.code == 0

    def test_agent_skips_lazy_init(self, tmp_path, monkeypatch):
        """'agent' command (config-facing) should not trigger lazy init."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        from kanibako.cli import main
        # 'agent list' should not crash with "not set up yet"
        with pytest.raises(SystemExit) as exc_info:
            main(["agent", "list"])
        assert exc_info.value.code == 0

    def test_box_fork_skips_lazy_init(self, tmp_path, monkeypatch):
        """'box fork' command should not trigger lazy init."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        # fork will fail with "no socket" but should NOT fail with lazy init
        from kanibako.cli import main
        with pytest.raises(SystemExit) as exc_info:
            main(["box", "fork", "test"])
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
        assert (tmp_path / "config" / "kanibako_config.yaml").exists()


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

    def test_help_contains_commands_section(self):
        parser = build_parser()
        assert "COMMANDS" in parser.epilog
        assert "SHORTCUTS" in parser.epilog
        # W2a: the image→rig / container→box ALIASES block was removed.
        assert "ALIASES" not in parser.epilog

    def test_help_description(self):
        parser = build_parser()
        assert parser.description == "Safe, persistent workspaces for AI coding agents."


class TestSetupNudge:
    """Gate-1 NON-BLOCKING setup-completion nudge (_setup_nudge)."""

    def _ns(self, command, **kw):
        import argparse

        return argparse.Namespace(command=command, **kw)

    def test_absent_marker_nudges_agent_command(self, tmp_path, capsys):
        """Agent-requiring command + absent marker → nudge on stderr; returns None."""
        from unittest.mock import patch

        from kanibako.cli import _setup_nudge

        cf = tmp_path / "kanibako_config.yaml"  # does not exist → absent marker
        with patch("kanibako.settings.config.config_file_path", return_value=cf), \
             patch("kanibako.settings.paths.xdg", return_value=tmp_path):
            result = _setup_nudge(self._ns("start"))
        assert result is None  # non-blocking: no raise, no exit
        err = capsys.readouterr().err
        assert "kanibako isn't set up yet" in err

    def test_absent_marker_nudge_stays_off_stdout(self, tmp_path, capsys):
        """The 'isn't set up yet' nag must be on STDERR, never STDOUT.

        On the remote machine path, ``kanibako start --print-container`` must
        keep stdout = the cname only; the setup nag is a warning and belongs on
        stderr so it cannot precede/pollute the parsed cname (rc7 item 7).
        """
        from unittest.mock import patch

        from kanibako.cli import _setup_nudge

        cf = tmp_path / "kanibako_config.yaml"  # absent marker
        with patch("kanibako.settings.config.config_file_path", return_value=cf), \
             patch("kanibako.settings.paths.xdg", return_value=tmp_path):
            _setup_nudge(self._ns("start"))
        captured = capsys.readouterr()
        assert "kanibako isn't set up yet" in captured.err
        assert captured.out == ""

    def test_nudge_band_prints_advisory_non_blocking(self, tmp_path, capsys):
        """NUDGE band ([BCV, FCV)) prints 'out of date' on stderr, no raise.

        The shipped constants collapse this band (BCV == FCV), so patch the build
        version + constants to put the 1.6.0 marker into [BCV, FCV).
        """
        from unittest.mock import patch

        import kanibako
        from kanibako.cli import _setup_nudge
        from kanibako.settings.config_interface import write_system_value

        cf = tmp_path / "kanibako_config.yaml"
        write_system_value(cf, "setup_completed", "1.6.0")
        with patch("kanibako.settings.config.config_file_path", return_value=cf), \
             patch("kanibako.settings.paths.xdg", return_value=tmp_path), \
             patch.object(kanibako, "__version__", "1.8.0"), \
             patch.object(kanibako, "SETUP_BCV", "1.5.0"), \
             patch.object(kanibako, "SETUP_FCV", "1.7.0"):
            # Non-blocking: prints, does not raise.
            assert _setup_nudge(self._ns("start")) is None
        assert "out of date" in capsys.readouterr().err

    def test_current_marker_no_nudge(self, tmp_path, capsys):
        from unittest.mock import patch

        from kanibako.cli import _setup_nudge
        from kanibako.settings.config_interface import write_system_value

        from packaging.version import Version

        import kanibako

        cf = tmp_path / "kanibako_config.yaml"
        # Marker == the current build's base version → == band → no nudge.
        write_system_value(
            cf, "setup_completed", Version(kanibako.__version__).base_version
        )
        with patch("kanibako.settings.config.config_file_path", return_value=cf), \
             patch("kanibako.settings.paths.xdg", return_value=tmp_path):
            _setup_nudge(self._ns("start"))
        assert capsys.readouterr().err == ""

    def test_agent_reauth_nudges(self, tmp_path, capsys):
        from unittest.mock import patch

        from kanibako.cli import _setup_nudge

        cf = tmp_path / "kanibako_config.yaml"
        with patch("kanibako.settings.config.config_file_path", return_value=cf), \
             patch("kanibako.settings.paths.xdg", return_value=tmp_path):
            _setup_nudge(self._ns("agent", agent_command="reauth"))
        assert "kanibako isn't set up yet" in capsys.readouterr().err

    def test_shell_never_nudges(self, tmp_path, capsys):
        """shell bypasses the nudge entirely (no agent resolution)."""
        from unittest.mock import patch

        from kanibako.cli import _setup_nudge

        cf = tmp_path / "kanibako_config.yaml"  # absent marker
        with patch("kanibako.settings.config.config_file_path", return_value=cf), \
             patch("kanibako.settings.paths.xdg", return_value=tmp_path):
            _setup_nudge(self._ns("shell"))
        assert capsys.readouterr().err == ""

    def test_setup_never_nudges(self, tmp_path, capsys):
        from unittest.mock import patch

        from kanibako.cli import _setup_nudge

        cf = tmp_path / "kanibako_config.yaml"
        with patch("kanibako.settings.config.config_file_path", return_value=cf), \
             patch("kanibako.settings.paths.xdg", return_value=tmp_path):
            _setup_nudge(self._ns("setup"))
        assert capsys.readouterr().err == ""

    def test_config_command_never_nudges(self, tmp_path, capsys):
        """Pure config/list commands are not agent-requiring → no nudge."""
        from unittest.mock import patch

        from kanibako.cli import _setup_nudge

        cf = tmp_path / "kanibako_config.yaml"
        with patch("kanibako.settings.config.config_file_path", return_value=cf), \
             patch("kanibako.settings.paths.xdg", return_value=tmp_path):
            _setup_nudge(self._ns("list"))
            _setup_nudge(self._ns("agent", agent_command="list"))
        assert capsys.readouterr().err == ""

    @staticmethod
    def _below_bcv():
        """A version string strictly below the live SETUP_BCV base version."""
        from packaging.version import Version

        import kanibako

        bcv = Version(kanibako.SETUP_BCV)
        below = f"{bcv.major - 1}.0.0" if bcv.major >= 1 else f"0.0.{bcv.micro}"
        if not Version(below) < Version(bcv.base_version):
            below = "0.0.1"
        assert Version(below) < Version(bcv.base_version)
        return below

    def test_too_old_marker_propagates_error(self, tmp_path):
        """ERROR band (ConfigVer < BCV) → KanibakoError propagates (rc1)."""
        from unittest.mock import patch

        from kanibako.cli import _setup_nudge
        from kanibako.settings.config_interface import write_system_value
        from kanibako.errors import KanibakoError

        cf = tmp_path / "kanibako_config.yaml"
        write_system_value(cf, "setup_completed", self._below_bcv())  # < BCV
        with patch("kanibako.settings.config.config_file_path", return_value=cf), \
             patch("kanibako.settings.paths.xdg", return_value=tmp_path):
            with pytest.raises(KanibakoError) as exc:
                _setup_nudge(self._ns("start"))
        assert "too old to auto-update" in str(exc.value)

    def test_newer_than_build_propagates_error(self, tmp_path):
        """ERROR band (ConfigVer > CurrentVer) → KanibakoError propagates (rc1)."""
        from unittest.mock import patch

        from packaging.version import Version

        import kanibako
        from kanibako.cli import _setup_nudge
        from kanibako.settings.config_interface import write_system_value
        from kanibako.errors import KanibakoError

        cf = tmp_path / "kanibako_config.yaml"
        # A version strictly greater than the build base → "from the future".
        newer = f"{Version(kanibako.__version__).major + 1}.0.0"
        assert Version(newer) > Version(Version(kanibako.__version__).base_version)
        write_system_value(cf, "setup_completed", newer)  # > build
        with patch("kanibako.settings.config.config_file_path", return_value=cf), \
             patch("kanibako.settings.paths.xdg", return_value=tmp_path):
            with pytest.raises(KanibakoError) as exc:
                _setup_nudge(self._ns("start"))
        assert "newer kanibako" in str(exc.value)

    def test_unexpected_failure_swallowed(self, tmp_path, capsys):
        """A non-KanibakoError failure is swallowed (gate never breaks a command)."""
        from unittest.mock import patch

        from kanibako.cli import _setup_nudge

        cf = tmp_path / "kanibako_config.yaml"
        with patch("kanibako.settings.config.config_file_path", return_value=cf), \
             patch("kanibako.settings.paths.xdg", return_value=tmp_path), \
             patch(
                 "kanibako.settings.config.setup_compat_gate",
                 side_effect=RuntimeError("boom"),
             ):
            # Must NOT raise — unexpected errors are swallowed.
            assert _setup_nudge(self._ns("start")) is None
        assert capsys.readouterr().err == ""

    def test_main_error_band_exits_rc1_with_clean_message(self, tmp_path, capsys):
        """E2E through main(): an ERROR band exits rc1 with a clean 'Error: …'.

        Drives the full main([...]) path (NOT _setup_nudge directly) to prove the
        gate's ConfigError is converted to the standard clean rc1 — not an
        uncaught traceback — since the _setup_nudge call sits OUTSIDE the func()
        KanibakoError handler.
        """
        from unittest.mock import patch

        from kanibako.cli import main
        from kanibako.settings.config_interface import write_system_value

        cf = tmp_path / "kanibako_config.yaml"
        # < BCV → ERROR band.
        write_system_value(cf, "setup_completed", self._below_bcv())
        with patch("kanibako.settings.config.config_file_path", return_value=cf), \
             patch("kanibako.settings.paths.xdg", return_value=tmp_path), \
             patch("kanibako.cli._ensure_initialized"):
            with pytest.raises(SystemExit) as exc:
                main(["start"])
        assert exc.value.code == 1  # clean rc1, not the interpreter default
        err = capsys.readouterr().err
        assert err.startswith("Error: ")  # the standard clean handling
        assert "too old to auto-update" in err

    def test_silent_bump_propagates_no_error_and_no_message(self, tmp_path, capsys):
        """SILENT-BUMP band via _setup_nudge: no message, no raise, marker bumped."""
        from unittest.mock import patch

        import kanibako
        from kanibako.cli import _setup_nudge
        from kanibako.settings.config import read_setup_completed
        from kanibako.settings.config_interface import write_system_value

        cf = tmp_path / "kanibako_config.yaml"
        write_system_value(cf, "setup_completed", "1.6.0")
        with patch("kanibako.settings.config.config_file_path", return_value=cf), \
             patch("kanibako.settings.paths.xdg", return_value=tmp_path), \
             patch.object(kanibako, "__version__", "1.8.0"), \
             patch.object(kanibako, "SETUP_BCV", "1.6.0"), \
             patch.object(kanibako, "SETUP_FCV", "1.6.0"):
            assert _setup_nudge(self._ns("start")) is None
            assert read_setup_completed(cf) == "1.8.0"
        assert capsys.readouterr().err == ""


class TestTemplateStalenessRetired:
    """R-38: the HARD template-staleness gate is GONE from ``_setup_nudge``.

    These are the FALSE-BLOCK cases the deleted gate produced — an initialized
    host whose config simply never carried a stamp (every host set up before the
    stamp existed) was hard-blocked out of every agent command.  Template drift is
    now announced by the setup bands instead (``SETUP_FCV`` nudge / ``SETUP_BCV``
    hard block), which is what ``TestSetupNudge`` covers.
    """

    def _ns(self, command, **kw):
        import argparse

        return argparse.Namespace(command=command, **kw)

    def _init_cf(self, tmp_path, stamp=None):
        """An initialized config (current setup marker) + an optional ORPHAN stamp."""
        from packaging.version import Version

        import kanibako
        from kanibako.settings.config_interface import write_system_value

        cf = tmp_path / "kanibako_config.yaml"
        write_system_value(
            cf, "setup_completed", Version(kanibako.__version__).base_version
        )
        if stamp is not None:
            write_system_value(cf, "templates_stamp", stamp)
        return cf

    @pytest.mark.parametrize("stamp", [None, "stale-digest"])
    def test_initialized_host_never_hard_blocks_on_templates(
        self, tmp_path, capsys, stamp
    ):
        """No stamp at all, or a stale one: neither raises and neither prints."""
        from unittest.mock import patch

        from kanibako.cli import _setup_nudge

        cf = self._init_cf(tmp_path, stamp=stamp)
        with patch("kanibako.settings.config.config_file_path", return_value=cf), \
             patch("kanibako.settings.paths.xdg", return_value=tmp_path):
            assert _setup_nudge(self._ns("start")) is None
        assert capsys.readouterr().err == ""

    def test_reauth_not_blocked_either(self, tmp_path):
        """The other agent-requiring command the old gate also blocked."""
        from unittest.mock import patch

        from kanibako.cli import _setup_nudge

        cf = self._init_cf(tmp_path, stamp="stale-digest")
        with patch("kanibako.settings.config.config_file_path", return_value=cf), \
             patch("kanibako.settings.paths.xdg", return_value=tmp_path):
            assert _setup_nudge(self._ns("agent", agent_command="reauth")) is None

    def test_first_run_init_writes_no_stamp(self, tmp_home):
        """_ensure_initialized installs the templates and records NO stamp (R-38).

        Pins the deleted writer on the REAL first-run path, and that the gated
        command immediately after it still runs (soft first-run nudge only).
        """
        from kanibako.cli import _ensure_initialized, _setup_nudge
        from kanibako.settings.config import config_file_path
        from kanibako.settings.config_io import load_doc
        from kanibako.settings.paths import xdg

        _ensure_initialized()
        cf = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
        assert "templates_stamp" not in (load_doc(cf).get("system") or {})
        # No patching: _setup_nudge resolves the same XDG-derived config file.
        # (setup marker is still absent → a soft nudge prints, but no hard error.)
        assert _setup_nudge(self._ns("start")) is None

    def test_first_run_writes_no_colorterm_setting_anywhere(self, tmp_home):
        """⚑ MBR-2/D1-4: THE WRITE IS GONE, at BOTH spellings it ever had.

        The value was seeded into the global docker ``.env`` FILE, then re-homed
        by B9/RQ-1 to ``system.env.COLORTERM``, then moved by ``6e3d016`` to
        ``box.env.COLORTERM`` — three storage locations for one default. It is
        declared in ``core-defaults.yaml`` now and stored NOWHERE: this asserts
        all three are absent, so restoring any one of them reds here as well as
        at the write-seam guard (``test_defaults_enforcement``).
        Delivery is pinned by
        ``test_declared_colorterm_reaches_the_resolved_container_env``.
        """
        from kanibako.cli import _ensure_initialized
        from kanibako.settings.config import config_file_path, load_config
        from kanibako.settings.config_io import load_doc
        from kanibako.settings.paths import load_std_paths, xdg

        _ensure_initialized()
        cf = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
        std = load_std_paths(load_config(cf))
        doc = load_doc(std.settings)
        assert "COLORTERM" not in ((doc.get("box") or {}).get("env") or {})
        assert "COLORTERM" not in ((doc.get("system") or {}).get("env") or {})
        # And NOT into the retired docker .env file.
        assert not (std.data_path / "env").exists()

    def test_first_run_leaves_a_stored_colorterm_value_untouched(self, tmp_home):
        """A user's own value survives init — now because nothing writes at all.

        It used to be create-if-absent ``setdefault`` semantics at the seam. The
        seam is gone, so the guarantee is structural rather than conditional; the
        pin stays because "a user value survives an upgrade" is the promise, and
        the way it is kept is an implementation detail that has already changed
        three times.
        """
        from kanibako.cli import _ensure_initialized
        from kanibako.settings.config import (
            KanibakoConfig, config_file_path, load_config,
        )
        from kanibako.settings.config_io import load_doc, write_nested_key
        from kanibako.settings.paths import load_std_paths, xdg

        # Materialise the config file the way a first run would, WITHOUT the
        # rest of init, then plant a user value and let init run for real.
        cf = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
        cf.parent.mkdir(parents=True, exist_ok=True)
        std = load_std_paths(KanibakoConfig())
        std.settings.parent.mkdir(parents=True, exist_ok=True)
        write_nested_key(std.settings, ("box", "env"), "COLORTERM", "mine")

        _ensure_initialized()
        std = load_std_paths(load_config(cf)) if cf.exists() else std
        assert load_doc(std.settings)["box"]["env"]["COLORTERM"] == "mine"

    def test_declared_colorterm_reaches_the_resolved_container_env(self, tmp_home):
        """⚑ THE DELIVERY PROOF for the DECLARED default (MBR-2/D1-4).

        ⚑ What changed here is the SOURCE, not the route: the value used to be a
        downward ``box:`` table written into the SYSTEM settings file and is a
        core-declared floor entry now, so the resolve is fed
        ``core_defaults.env_default_categories()`` where it used to be fed ``{}``
        and a written file. "Declared" is not "delivered" — so drive it all the
        way through the REAL launch route the container env is built from::

            build_launch_snapshot -> snapshot_category_entries
            -> collapse_env -> start._build_config_env

        and assert ``COLORTERM=truecolor`` arrives, carrying the ``box`` scope. A
        ``config show --effective`` check would NOT substitute: it shares code
        with the display path B9 changed the same day.

        ⚑ THE PROVENANCE ASSERT MOVED WITH THE ROUTE, not away from it. It read the
        winning ``CategoryEntry`` off ``LaunchDeliveries.envs``; that carrier is
        retired, and the winner's ``(scope, key)`` rides the COLLAPSED SLOT now —
        the same pair, at the seam a display or a diagnostic actually reads.
        """
        from kanibako.cli import _ensure_initialized
        from kanibako.commands.start import _build_config_env
        from kanibako.settings import core_defaults
        from kanibako.settings.config import config_file_path, load_config
        from kanibako.settings.paths import load_std_paths, xdg
        from kanibako.settings.settings_launch import (
            build_launch_snapshot,
            snapshot_category_entries,
        )
        from kanibako.settings.settings_resolve import ResolveCtx
        from kanibako.settings.store_collapse import collapse_env

        _ensure_initialized()
        cf = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
        std = load_std_paths(load_config(cf))

        ctx = ResolveCtx(
            agent_name="claude", workset_name=None, host_home=str(tmp_home),
            xdg={"XDG_DATA_HOME": str(tmp_home / "data")}, config={},
        )
        # NO settings file supplies it — the floor is the only source, which is
        # the whole claim: the value reaches a box that has stored nothing.
        snap = build_launch_snapshot(
            agent_name="claude", ctx=ctx, system_path=std.settings,
            agent_path=None, workset_path=None, box_path=None,
            default_categories=core_defaults.env_default_categories(),
        )
        entries = snapshot_category_entries(
            snap, active_agent="claude", box_ctx=ctx,
        )
        slots = collapse_env(entries)
        assert _build_config_env(slots)["COLORTERM"] == "truecolor"
        winner = slots["COLORTERM"]
        assert (winner.scope, winner.key) == ("box", "box.env.COLORTERM")

    def test_colorterm_box_file_override_beats_the_declared_default(self, tmp_home):
        """A box's OWN ``box.env.COLORTERM`` still wins over the DECLARED default.

        The declaration only makes sense if it stays overridable at the scope it
        is declared for — a default nothing can beat is a hardwired value. ⚑ The
        override must be spelled at the SAME scope: a ``system.env.COLORTERM``
        would not win, it would REFUSE the launch as a contested slot.
        """
        from kanibako.cli import _ensure_initialized
        from kanibako.commands.start import _build_config_env
        from kanibako.settings import core_defaults
        from kanibako.settings.config import config_file_path, load_config
        from kanibako.settings.config_io import write_nested_key
        from kanibako.settings.paths import load_std_paths, xdg
        from kanibako.settings.settings_launch import (
            build_launch_snapshot,
            snapshot_category_entries,
        )
        from kanibako.settings.settings_resolve import ResolveCtx
        from kanibako.settings.store_collapse import collapse_env

        _ensure_initialized()
        cf = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
        std = load_std_paths(load_config(cf))

        box_settings = tmp_home / "abox" / "settings.yaml"
        box_settings.parent.mkdir(parents=True, exist_ok=True)
        write_nested_key(box_settings, ("box", "env"), "COLORTERM", "256color")

        ctx = ResolveCtx(
            agent_name="claude", workset_name=None, host_home=str(tmp_home),
            xdg={"XDG_DATA_HOME": str(tmp_home / "data")}, config={},
        )
        snap = build_launch_snapshot(
            agent_name="claude", ctx=ctx, system_path=std.settings,
            agent_path=None, workset_path=None, box_path=box_settings,
            default_categories=core_defaults.env_default_categories(),
        )
        slots = collapse_env(
            snapshot_category_entries(snap, active_agent="claude", box_ctx=ctx),
        )
        assert _build_config_env(slots)["COLORTERM"] == "256color"


class TestShellAgentFlagIgnored:
    """shell + --agent is IGNORED with a note (not a hard FlagRelevanceError)."""

    def test_shell_with_agent_does_not_raise_and_notes(self, capsys):
        import argparse

        from kanibako.commands.flags import check_flag_relevance

        ns = argparse.Namespace(command="shell", agent="goose", box=None)
        # Must NOT raise.
        check_flag_relevance(ns)
        err = capsys.readouterr().err
        assert "--agent is ignored for 'shell'" in err

    def test_box_shell_with_agent_does_not_raise_and_notes(self, capsys):
        import argparse

        from kanibako.commands.flags import check_flag_relevance

        ns = argparse.Namespace(
            command="box", box_command="shell", agent="goose", box=None,
        )
        check_flag_relevance(ns)
        err = capsys.readouterr().err
        assert "--agent is ignored for 'shell'" in err

    def test_shell_with_agent_and_box_both_ok(self, capsys):
        """--box is relevant for shell; combined with --agent must still pass."""
        import argparse

        from kanibako.commands.flags import check_flag_relevance

        ns = argparse.Namespace(command="shell", agent="goose", box="myproj")
        check_flag_relevance(ns)  # no raise
        assert "--agent is ignored for 'shell'" in capsys.readouterr().err

    def test_unrelated_command_with_agent_still_raises(self):
        """--agent for a non-agent, non-shell command STILL hard-errors."""
        import argparse

        from kanibako.commands.flags import (
            FlagRelevanceError,
            check_flag_relevance,
        )

        ns = argparse.Namespace(command="list", agent="goose", box=None)
        with pytest.raises(FlagRelevanceError):
            check_flag_relevance(ns)


class TestBoxConfigVerbsAcceptBoxFlag:
    """Regression (B1): every box config verb accepts ``--box`` as the subject
    selector — the shared dispatch reads ``args.box`` via resolve_subject_value,
    so set/reset/get/show must all be in BOX_FLAG_COMMANDS or check_flag_relevance
    hard-errors before the handler runs."""

    @pytest.mark.parametrize("verb", ["set", "reset", "get", "show"])
    def test_box_verb_with_box_flag_does_not_raise(self, verb):
        import argparse

        from kanibako.commands.flags import check_flag_relevance

        ns = argparse.Namespace(
            command="box", box_command=verb, agent=None, box="myproj",
        )
        # Must NOT raise FlagRelevanceError (would, if the verb weren't whitelisted).
        check_flag_relevance(ns)

    def test_box_set_with_box_routes_to_named_subject(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """End-to-end: ``box set --box <proj> <key>=<value>`` routes the set to the
        named subject box (not cwd) and writes its override."""
        import argparse

        from kanibako.commands.box._parser import run_set
        from kanibako.settings.config import load_config, load_project_overrides
        from kanibako.settings.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        src = tmp_home / "subjectproj"
        src.mkdir()
        project_dir = str(src)
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # ``--box`` names the subject; positional args carry only the key=value.
        args = argparse.Namespace(
            args=["box.image=subject-img:v1"], force=False,
            box=project_dir,
        )
        rc = run_set(args)
        assert rc == 0
        assert "subject-img:v1" in capsys.readouterr().out

        # The override landed in the NAMED subject's settings file.
        overrides = load_project_overrides(proj.metadata_path / "settings.yaml")
        assert overrides.get("box_image") == "subject-img:v1"

    def test_shell_never_nudges_after_reorder(self, tmp_path, capsys):
        """Regression: shell stays nudge-silent (Gate-1 excludes it)."""
        import argparse
        from unittest.mock import patch

        from kanibako.cli import _setup_nudge

        cf = tmp_path / "kanibako_config.yaml"  # absent marker
        with patch("kanibako.settings.config.config_file_path", return_value=cf), \
             patch("kanibako.settings.paths.xdg", return_value=tmp_path):
            _setup_nudge(argparse.Namespace(command="shell"))
        assert capsys.readouterr().err == ""


class TestNullFlagEndToEnd:
    """B-5, whole path: argv -> parser -> run_set -> settings file.

    The per-verb parse matrix lives in ``test_commands/test_flags.py``.  This
    pins the END of the path too, because a parse fix that stranded the flag
    before the writer would look green there and still be broken in the box.
    """

    def _settings_for(self, tmp_home, config_file, name, argv_tail):
        from kanibako.cli import build_parser
        from kanibako.settings.config import load_config
        from kanibako.settings.config_io import load_doc
        from kanibako.settings.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        src = tmp_home / name
        src.mkdir()
        proj = resolve_project(std, config, project_dir=str(src), initialize=True)

        args = build_parser().parse_args(
            ["box", "set"] + [str(src) if a is None else a for a in argv_tail]
        )
        assert args.func(args) == 0
        return load_doc(proj.metadata_path / "settings.yaml")

    def test_every_ordering_writes_the_same_null(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        # ``None`` stands in for the box path (filled per-case).  The middle
        # ordering is the one the P7 bifrost run recorded as broken.
        orderings = {
            "leading": ["--null", None, "pref.system.agent"],
            "middle": [None, "--null", "pref.system.agent"],
            "trailing": [None, "pref.system.agent", "--null"],
        }
        written = {
            label: self._settings_for(tmp_home, config_file, f"nullproj-{label}", tail)
            for label, tail in orderings.items()
        }
        for label, doc in written.items():
            assert "agent" in doc["pref"]["system"], label
            assert doc["pref"]["system"]["agent"] is None, label
        # Equivalence, not just success: the position of a flag may not change
        # what lands on disk.
        assert written["middle"] == written["leading"] == written["trailing"]

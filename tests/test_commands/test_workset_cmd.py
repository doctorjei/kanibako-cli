"""Tests for kanibako.commands.workset_cmd."""

from __future__ import annotations

import argparse


from kanibako.config import load_config
from kanibako.paths import load_std_paths
from kanibako.workset import (
    add_project,
    create_workset,
)


class TestWorksetCreate:
    def test_create_success(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_create

        ws_root = tmp_home / "myworkset"
        args = argparse.Namespace(
            path=str(ws_root), name=None,
            standalone=False, image=None, no_vault=False, distinct_auth=False,
        )
        rc = run_create(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Created working set" in out
        assert ws_root.resolve().is_dir()

    def test_create_with_name_override(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_create

        ws_root = tmp_home / "myworkset2"
        args = argparse.Namespace(
            path=str(ws_root), name="custom-name",
            standalone=False, image=None, no_vault=False, distinct_auth=False,
        )
        rc = run_create(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "custom-name" in out

    def test_create_defaults_to_cwd(self, config_file, tmp_home, capsys, monkeypatch):
        from kanibako.commands.workset_cmd import run_create

        ws_dir = tmp_home / "cwd_ws"
        ws_dir.mkdir()
        monkeypatch.chdir(ws_dir)
        # Since cwd exists and create_workset errors on existing root,
        # test that path=None uses cwd by checking the error message
        args = argparse.Namespace(
            path=None, name="cwdws",
            standalone=False, image=None, no_vault=False, distinct_auth=False,
        )
        rc = run_create(args)
        # cwd already exists, so this should fail with "already exists"
        assert rc == 1
        err = capsys.readouterr().err
        assert "already exists" in err

    def test_create_duplicate_name_error(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_create

        ws_root1 = tmp_home / "ws1"
        args1 = argparse.Namespace(
            path=str(ws_root1), name="dup",
            standalone=False, image=None, no_vault=False, distinct_auth=False,
        )
        run_create(args1)

        ws_root2 = tmp_home / "ws2"
        args2 = argparse.Namespace(
            path=str(ws_root2), name="dup",
            standalone=False, image=None, no_vault=False, distinct_auth=False,
        )
        rc = run_create(args2)
        assert rc == 1
        err = capsys.readouterr().err
        assert "already registered" in err

    def test_create_existing_root_error(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_create

        ws_root = tmp_home / "existing"
        ws_root.mkdir()
        args = argparse.Namespace(
            path=str(ws_root), name="ex",
            standalone=False, image=None, no_vault=False, distinct_auth=False,
        )
        rc = run_create(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "already exists" in err

    def test_create_with_distinct_auth(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_create

        ws_root = tmp_home / "distinct_ws"
        args = argparse.Namespace(
            path=str(ws_root), name="distinctws",
            standalone=False, image=None, no_vault=False, distinct_auth=True,
        )
        rc = run_create(args)
        assert rc == 0

        # Verify auth mode is set to distinct
        from kanibako.workset import load_workset
        ws = load_workset(ws_root.resolve())
        assert ws.auth == "distinct"

    def test_create_with_image(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_create

        ws_root = tmp_home / "image_ws"
        args = argparse.Namespace(
            path=str(ws_root), name="imagews",
            standalone=False, image="custom:latest", no_vault=False, distinct_auth=False,
        )
        rc = run_create(args)
        assert rc == 0

        # Verify config.toml was written with image
        import tomllib
        config_toml = ws_root.resolve() / "config.toml"
        assert config_toml.exists()
        with open(config_toml, "rb") as f:
            data = tomllib.load(f)
        assert data["container_image"] == "custom:latest"


class TestWorksetList:
    def test_list_empty(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_list

        args = argparse.Namespace(quiet=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "No working sets" in out

    def test_list_shows_worksets(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_list

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("alpha", tmp_home / "ws_alpha", std)

        args = argparse.Namespace(quiet=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "alpha" in out
        assert "NAME" in out

    def test_list_shows_project_count(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_list

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("beta", tmp_home / "ws_beta", std)

        src = tmp_home / "proj_src"
        src.mkdir()
        add_project(ws, "myproj", src)

        args = argparse.Namespace(quiet=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "beta" in out
        assert "1" in out

    def test_list_quiet(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_list

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("quiet1", tmp_home / "ws_quiet1", std)
        create_workset("quiet2", tmp_home / "ws_quiet2", std)

        args = argparse.Namespace(quiet=True)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        lines = out.strip().split("\n")
        assert len(lines) == 2
        assert "quiet1" in lines
        assert "quiet2" in lines
        # Quiet mode should not have header
        assert "NAME" not in out

    def test_list_quiet_empty(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_list

        args = argparse.Namespace(quiet=True)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert out == ""


class TestWorksetRm:
    def test_rm_success(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_rm

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("todel", tmp_home / "ws_todel", std)

        args = argparse.Namespace(name="todel", purge=False, force=True)
        rc = run_rm(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Deleted working set 'todel'" in out

    def test_rm_with_purge(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_rm

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("rmfiles", tmp_home / "ws_rmfiles", std)
        root = ws.root

        assert root.is_dir()
        args = argparse.Namespace(name="rmfiles", purge=True, force=True)
        rc = run_rm(args)
        assert rc == 0
        assert not root.is_dir()

    def test_rm_unknown_error(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_rm

        args = argparse.Namespace(name="nonexistent", purge=False, force=True)
        rc = run_rm(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "not registered" in err

    def test_rm_with_projects_errors_without_force(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_rm

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("hasproj", tmp_home / "ws_hasproj", std)

        src = tmp_home / "proj_src_rm"
        src.mkdir()
        add_project(ws, "myproj", src)

        args = argparse.Namespace(name="hasproj", purge=False, force=False)
        rc = run_rm(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "has 1 project(s)" in err
        assert "--force" in err

    def test_rm_with_projects_succeeds_with_force(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_rm

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("hasproj2", tmp_home / "ws_hasproj2", std)

        src = tmp_home / "proj_src_rm2"
        src.mkdir()
        add_project(ws, "myproj", src)

        args = argparse.Namespace(name="hasproj2", purge=False, force=True)
        rc = run_rm(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Deleted working set 'hasproj2'" in out


class TestWorksetConnect:
    def test_connect_success(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_connect

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("addws", tmp_home / "ws_add", std)

        src = tmp_home / "add_src"
        src.mkdir()

        args = argparse.Namespace(
            workset="addws", source=str(src), project_name=None,
        )
        rc = run_connect(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Added project" in out
        assert "add_src" in out

    def test_connect_defaults_to_cwd(self, config_file, tmp_home, capsys, monkeypatch):
        from kanibako.commands.workset_cmd import run_connect

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("cwdws", tmp_home / "ws_cwd", std)

        cwd_dir = tmp_home / "cwd_proj"
        cwd_dir.mkdir()
        monkeypatch.chdir(cwd_dir)

        args = argparse.Namespace(
            workset="cwdws", source=None, project_name=None,
        )
        rc = run_connect(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "cwd_proj" in out

    def test_connect_custom_name(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_connect

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("namews", tmp_home / "ws_name", std)

        src = tmp_home / "name_src"
        src.mkdir()

        args = argparse.Namespace(
            workset="namews", source=str(src), project_name="custom-name",
        )
        rc = run_connect(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "custom-name" in out

    def test_connect_duplicate_error(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_connect

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("dupws", tmp_home / "ws_dup", std)

        src = tmp_home / "dup_src"
        src.mkdir()
        add_project(ws, "proj1", src)

        args = argparse.Namespace(
            workset="dupws", source=str(src), project_name="proj1",
        )
        rc = run_connect(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "already exists" in err


class TestWorksetDisconnect:
    def test_disconnect_success(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_disconnect

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("rmws", tmp_home / "ws_rm", std)

        src = tmp_home / "rm_src"
        src.mkdir()
        add_project(ws, "rmproj", src)

        args = argparse.Namespace(
            workset="rmws", project="rmproj",
            remove_files=False, force=True,
        )
        rc = run_disconnect(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Removed project 'rmproj'" in out

    def test_disconnect_with_files(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_disconnect

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("rmfws", tmp_home / "ws_rmf", std)

        src = tmp_home / "rmf_src"
        src.mkdir()
        add_project(ws, "rmfproj", src)

        # Verify per-project dirs were created.
        assert (ws.projects_dir / "rmfproj").is_dir()

        args = argparse.Namespace(
            workset="rmfws", project="rmfproj",
            remove_files=True, force=True,
        )
        rc = run_disconnect(args)
        assert rc == 0
        assert not (ws.projects_dir / "rmfproj").is_dir()

    def test_disconnect_unknown_error(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_disconnect

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("rmunkws", tmp_home / "ws_rmunk", std)

        args = argparse.Namespace(
            workset="rmunkws", project="nope",
            remove_files=False, force=True,
        )
        rc = run_disconnect(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "not found" in err


class TestWorksetInfo:
    def test_info_success(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_info

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("infows", tmp_home / "ws_info", std)

        src = tmp_home / "info_src"
        src.mkdir()
        add_project(ws, "infoproj", src)

        args = argparse.Namespace(name="infows")
        rc = run_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "infows" in out
        assert "infoproj" in out
        assert "Root:" in out
        assert "Created:" in out

    def test_info_unknown_error(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_info

        args = argparse.Namespace(name="nosuchws")
        rc = run_info(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "not registered" in err

    def test_info_shows_auth(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_create, run_info

        ws_root = tmp_home / "authws"
        run_create(argparse.Namespace(
            path=str(ws_root), name="authws",
            standalone=False, image=None, no_vault=False, distinct_auth=False,
        ))
        capsys.readouterr()

        args = argparse.Namespace(name="authws")
        rc = run_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Auth:" in out
        assert "shared" in out


class TestWorksetConfig:
    def test_config_show_empty(self, config_file, tmp_home, capsys):
        """Config show with no overrides prints '(no overrides)'."""
        from kanibako.commands.workset_cmd import run_config

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("cfgws", tmp_home / "ws_cfg", std)

        args = argparse.Namespace(
            workset="cfgws", key_value=None,
            effective=False, reset=None, reset_all=False,
            force=False, local=False,
        )
        rc = run_config(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "no overrides" in out

    def test_config_get_auth(self, config_file, tmp_home, capsys):
        """Getting auth key returns value from workset.toml."""
        from kanibako.commands.workset_cmd import run_config

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("authcfg", tmp_home / "ws_authcfg", std)

        args = argparse.Namespace(
            workset="authcfg", key_value="auth",
            effective=False, reset=None, reset_all=False,
            force=False, local=False,
        )
        rc = run_config(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "shared" in out

    def test_config_set_auth_distinct(self, config_file, tmp_home, capsys):
        """Setting auth=distinct updates workset.toml and clears credentials."""
        from kanibako.commands.workset_cmd import run_config
        from unittest.mock import MagicMock, patch

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("setauth", tmp_home / "ws_setauth", std)

        mock_target = MagicMock()
        mock_target.invalidate_credentials.return_value = None

        args = argparse.Namespace(
            workset="setauth", key_value="auth=distinct",
            effective=False, reset=None, reset_all=False,
            force=False, local=False,
        )
        with patch(
            "kanibako.targets.resolve_target",
            return_value=mock_target,
        ):
            rc = run_config(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "distinct" in out

        # Verify workset.toml was updated
        from kanibako.workset import load_workset
        ws = load_workset((tmp_home / "ws_setauth").resolve())
        assert ws.auth == "distinct"

    def test_config_set_auth_invalid(self, config_file, tmp_home, capsys):
        """Setting auth to invalid value produces error."""
        from kanibako.commands.workset_cmd import run_config

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("badauth", tmp_home / "ws_badauth", std)

        args = argparse.Namespace(
            workset="badauth", key_value="auth=bogus",
            effective=False, reset=None, reset_all=False,
            force=False, local=False,
        )
        rc = run_config(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "shared" in err or "distinct" in err

    def test_config_set_regular_key(self, config_file, tmp_home, capsys):
        """Setting a regular config key writes to config.toml."""
        from kanibako.commands.workset_cmd import run_config

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("regcfg", tmp_home / "ws_regcfg", std)

        args = argparse.Namespace(
            workset="regcfg", key_value="container_image=myimage:latest",
            effective=False, reset=None, reset_all=False,
            force=False, local=False,
        )
        rc = run_config(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Set" in out
        assert "container_image" in out

    def test_config_reset_key(self, config_file, tmp_home, capsys):
        """Resetting a config key removes the override."""
        from kanibako.commands.workset_cmd import run_config

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("resetcfg", tmp_home / "ws_resetcfg", std)

        # First set a value.
        set_args = argparse.Namespace(
            workset="resetcfg", key_value="container_image=myimage:latest",
            effective=False, reset=None, reset_all=False,
            force=False, local=False,
        )
        run_config(set_args)
        capsys.readouterr()

        # Then reset it.
        reset_args = argparse.Namespace(
            workset="resetcfg", key_value=None,
            effective=False, reset="container_image", reset_all=False,
            force=False, local=False,
        )
        rc = run_config(reset_args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Reset" in out or "No override" in out

    def test_config_reset_auth(self, config_file, tmp_home, capsys):
        """Resetting auth key reverts to shared."""
        from kanibako.commands.workset_cmd import run_config
        from unittest.mock import MagicMock, patch

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("resetauth", tmp_home / "ws_resetauth", std)

        # First set to distinct.
        mock_target = MagicMock()
        mock_target.invalidate_credentials.return_value = None
        set_args = argparse.Namespace(
            workset="resetauth", key_value="auth=distinct",
            effective=False, reset=None, reset_all=False,
            force=False, local=False,
        )
        with patch("kanibako.targets.resolve_target", return_value=mock_target):
            run_config(set_args)
        capsys.readouterr()

        # Then reset.
        reset_args = argparse.Namespace(
            workset="resetauth", key_value=None,
            effective=False, reset="auth", reset_all=False,
            force=False, local=False,
        )
        rc = run_config(reset_args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "shared" in out

        from kanibako.workset import load_workset
        ws = load_workset((tmp_home / "ws_resetauth").resolve())
        assert ws.auth == "shared"

    def test_config_reset_all(self, config_file, tmp_home, capsys):
        """--reset --all clears all overrides."""
        from kanibako.commands.workset_cmd import run_config

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("resetall", tmp_home / "ws_resetall", std)

        # Set a value first.
        set_args = argparse.Namespace(
            workset="resetall", key_value="container_image=myimage:latest",
            effective=False, reset=None, reset_all=False,
            force=False, local=False,
        )
        run_config(set_args)
        capsys.readouterr()

        # Reset all.
        reset_args = argparse.Namespace(
            workset="resetall", key_value=None,
            effective=False, reset="__ALL__", reset_all=True,
            force=True, local=False,
        )
        rc = run_config(reset_args)
        assert rc == 0

    def test_config_unknown_workset(self, config_file, tmp_home, capsys):
        """Config on unknown workset returns error."""
        from kanibako.commands.workset_cmd import run_config

        args = argparse.Namespace(
            workset="nosuchws", key_value=None,
            effective=False, reset=None, reset_all=False,
            force=False, local=False,
        )
        rc = run_config(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "not registered" in err


class TestWorksetParser:
    """Test parser aliases and subcommand registration."""

    def test_aliases_registered(self):
        """Verify that ls, inspect, and delete aliases are registered."""
        import argparse
        from kanibako.commands.workset_cmd import add_parser

        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers()
        add_parser(subs)

        # These should parse without error (aliases are recognized).
        # We test by parsing a few known alias forms.
        args = parser.parse_args(["workset", "ls"])
        assert hasattr(args, "func")

        args = parser.parse_args(["workset", "inspect", "myws"])
        assert hasattr(args, "func")

        args = parser.parse_args(["workset", "delete", "myws", "--force"])
        assert hasattr(args, "func")

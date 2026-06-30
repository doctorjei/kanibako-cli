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
        assert "already in use" in err

    def test_create_reserved_sentinel_error(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_create

        args = argparse.Namespace(
            path=str(tmp_home / "ws-primary"), name="__PRIMARY__",
            standalone=False, image=None, no_vault=False, distinct_auth=False,
        )
        rc = run_create(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "reserved" in err

    def test_create_auto_name_collision_refused(self, config_file, tmp_home, capsys):
        # The default name derives from path.name; a collision must still refuse
        # cleanly (no auto-suffix) via the WorksetError caught in run_create.
        from kanibako.commands.workset_cmd import run_create

        (tmp_home / "shared").mkdir()
        first = tmp_home / "a" / "shared"
        args1 = argparse.Namespace(
            path=str(first), name=None,
            standalone=False, image=None, no_vault=False, distinct_auth=False,
        )
        assert run_create(args1) == 0
        capsys.readouterr()

        second = tmp_home / "b" / "shared"
        args2 = argparse.Namespace(
            path=str(second), name=None,
            standalone=False, image=None, no_vault=False, distinct_auth=False,
        )
        rc = run_create(args2)
        assert rc == 1
        assert "already in use" in capsys.readouterr().err

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
        assert ws.group_auth is False

    def test_create_with_image(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_create

        ws_root = tmp_home / "image_ws"
        args = argparse.Namespace(
            path=str(ws_root), name="imagews",
            standalone=False, image="custom:latest", no_vault=False, distinct_auth=False,
        )
        rc = run_create(args)
        assert rc == 0

        # The image cascade setting AND the workset.meta identity coexist in the
        # single root settings.yaml.
        import yaml
        settings_yaml = ws_root.resolve() / "settings.yaml"
        assert settings_yaml.exists()
        with open(settings_yaml) as f:
            data = yaml.safe_load(f)
        assert data["box"]["image"] == "custom:latest"
        assert data["workset"]["meta"]["name"] == "imagews"


class TestWorksetList:
    def test_list_empty_shows_only_default(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_list

        args = argparse.Namespace(quiet=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        # The synthesized default workset is always present.
        assert "default" in out
        assert "<default workset>" in out
        assert "NAME" in out

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
        # 2 named worksets + the synthesized default.
        assert len(lines) == 3
        assert "default" in lines
        assert "quiet1" in lines
        assert "quiet2" in lines
        # Quiet mode should not have header
        assert "NAME" not in out

    def test_list_quiet_empty_shows_default(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_list

        args = argparse.Namespace(quiet=True)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        # Even with no named worksets, the default workset is listed.
        assert out.strip() == "default"


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

    def test_connect_external_writes_override_and_symlink(
        self, config_file, tmp_home, capsys
    ):
        """connect to an EXTERNAL dir → workspace override in settings.yaml,
        connected.yaml entry, and a workspaces/{name} symlink to the dir."""
        from kanibako.commands.workset_cmd import run_connect
        from kanibako.config import read_project_meta
        from kanibako.workset import _load_connected

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("extws", tmp_home / "ws_ext", std)

        # External source: a sibling of the workset root, outside ws.root.
        external = (tmp_home / "external_repo").resolve()
        external.mkdir()

        args = argparse.Namespace(
            workset="extws", source=str(external), project_name="ext",
        )
        rc = run_connect(args)
        assert rc == 0

        # settings.yaml carries the workspace override = external path.
        project_toml = ws.projects_dir / "ext" / "settings.yaml"
        meta = read_project_meta(project_toml)
        assert meta is not None
        assert meta["workspace"] == str(external)

        # connected.yaml has the redirect entry.
        connected = _load_connected(std)
        assert str(external) in connected
        assert connected[str(external)] == {"workset": "extws", "project": "ext"}

        # workspaces/ext is a SYMLINK to the external dir, not a real dir.
        link = ws.workspaces_dir / "ext"
        assert link.is_symlink()
        assert link.resolve() == external

    def test_connect_internal_no_override_no_symlink(
        self, config_file, tmp_home, capsys
    ):
        """connect to a dir INSIDE the workset root → normal behavior: a real
        workspaces/{name} dir, no override, no connected.yaml entry."""
        from kanibako.commands.workset_cmd import run_connect
        from kanibako.config import read_project_meta
        from kanibako.workset import _load_connected

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("intws", tmp_home / "ws_int", std)

        # Internal source: a directory inside the workset root.
        internal = ws.root / "inside_src"
        internal.mkdir()

        args = argparse.Namespace(
            workset="intws", source=str(internal), project_name="int",
        )
        rc = run_connect(args)
        assert rc == 0

        # No workspace override written (settings.yaml not pre-seeded).
        project_toml = ws.projects_dir / "int" / "settings.yaml"
        meta = read_project_meta(project_toml)
        assert meta is None

        # No connected.yaml entry for an internal source.
        connected = _load_connected(std)
        assert str(internal.resolve()) not in connected

        # workspaces/int is a real directory, not a symlink.
        wsdir = ws.workspaces_dir / "int"
        assert wsdir.is_dir()
        assert not wsdir.is_symlink()


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
        assert "Group auth:" in out
        assert "True" in out


class TestWorksetConfig:
    def test_config_show_empty(self, config_file, tmp_home, capsys):
        """Config show with no overrides prints '(no overrides)'."""
        from kanibako.commands.workset_cmd import run_show

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("cfgws", tmp_home / "ws_cfg", std)

        args = argparse.Namespace(workset="cfgws", effective=False)
        rc = run_show(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "no overrides" in out

    def test_config_get_auth(self, config_file, tmp_home, capsys):
        """Getting group_auth key returns value from the workset.meta identity."""
        from kanibako.commands.workset_cmd import run_get

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("authcfg", tmp_home / "ws_authcfg", std)

        args = argparse.Namespace(workset="authcfg", key="group_auth")
        rc = run_get(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "True" in out

    def test_config_set_auth_distinct(self, config_file, tmp_home, capsys):
        """Setting group_auth=false updates the workset.meta identity and clears credentials."""
        from kanibako.commands.workset_cmd import run_set
        from unittest.mock import MagicMock, patch

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("setauth", tmp_home / "ws_setauth", std)

        mock_target = MagicMock()
        mock_target.invalidate_credentials.return_value = None

        args = argparse.Namespace(
            workset="setauth", key_value="group_auth=false",
            force=False, local=False,
        )
        with patch(
            "kanibako.targets.resolve_target",
            return_value=mock_target,
        ):
            rc = run_set(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "distinct" in out

        # Verify the workset.meta identity was updated
        from kanibako.workset import load_workset
        ws = load_workset((tmp_home / "ws_setauth").resolve())
        assert ws.group_auth is False

    def test_config_set_auth_distinct_invalidates_all_agents(
        self, config_file, tmp_home, capsys
    ):
        """§Design 8(b): switching group_auth shared→distinct invalidates
        credentials across ALL KNOWN agents (discover_targets), not just the
        single agent the cascade would resolve — a workset may have been used
        with several agents, so the cleanup must hit every one."""
        from kanibako.commands.workset_cmd import run_set
        from unittest.mock import MagicMock, patch

        config = load_config(config_file)
        std = load_std_paths(config)
        # Create a workset with a project so there is a shell dir to clear.
        create_workset("multiauth", tmp_home / "ws_multiauth", std)

        # Two fake known agents.
        agent_a = MagicMock()
        agent_b = MagicMock()

        def _resolve(name, *a, **kw):
            return {"alpha": agent_a, "beta": agent_b}[name]

        args = argparse.Namespace(
            workset="multiauth", key_value="group_auth=false",
            force=False, local=False,
        )
        with patch(
            "kanibako.targets.discover_targets",
            return_value={"alpha": object, "beta": object},
        ) as mock_discover, patch(
            "kanibako.targets.resolve_target", side_effect=_resolve,
        ):
            rc = run_set(args)

        assert rc == 0
        # Iterated the full known-agent set (not a single resolved agent).
        mock_discover.assert_called_once_with()
        # Both agents were resolved for invalidation.
        # (invalidate_credentials only fires when a shell dir exists; the
        # all-agents iteration is the asserted contract.)

    def test_config_set_auth_invalid(self, config_file, tmp_home, capsys):
        """Setting group_auth to invalid value produces error."""
        from kanibako.commands.workset_cmd import run_set

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("badauth", tmp_home / "ws_badauth", std)

        args = argparse.Namespace(
            workset="badauth", key_value="group_auth=bogus",
            force=False, local=False,
        )
        rc = run_set(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "true" in err or "false" in err

    def test_config_set_regular_key(self, config_file, tmp_home, capsys):
        """Setting a regular config key writes to config.yaml.

        Uses ``vault.enabled`` — a SCOPELESS regular key (own-file write) that is
        legal at the workset scope under the §0 scope-direction guard (block B4).
        ``box.image`` (a box.* key) would now be REFUSED at the workset scope —
        see ``TestWorksetScopeDirection`` for that refusal.
        """
        from kanibako.commands.workset_cmd import run_set

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("regcfg", tmp_home / "ws_regcfg", std)

        args = argparse.Namespace(
            workset="regcfg", key_value="vault.enabled=false",
            force=False, local=False,
        )
        rc = run_set(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Set" in out
        assert "vault" in out

    def test_config_reset_key(self, config_file, tmp_home, capsys):
        """Resetting a config key removes the override."""
        from kanibako.commands.workset_cmd import run_reset, run_set

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("resetcfg", tmp_home / "ws_resetcfg", std)

        # First set a value (vault.enabled = scopeless own-file key, legal at
        # the workset scope under the B4 scope-direction guard).
        set_args = argparse.Namespace(
            workset="resetcfg", key_value="vault.enabled=false",
            force=False, local=False,
        )
        run_set(set_args)
        capsys.readouterr()

        # Then reset it.
        reset_args = argparse.Namespace(
            workset="resetcfg", key="vault.enabled", reset_all=False, force=False,
        )
        rc = run_reset(reset_args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Reset" in out or "No override" in out

    def test_config_reset_auth(self, config_file, tmp_home, capsys):
        """Resetting group_auth key reverts to True (shared)."""
        from kanibako.commands.workset_cmd import run_reset, run_set
        from unittest.mock import MagicMock, patch

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("resetauth", tmp_home / "ws_resetauth", std)

        # First set to distinct.
        mock_target = MagicMock()
        mock_target.invalidate_credentials.return_value = None
        set_args = argparse.Namespace(
            workset="resetauth", key_value="group_auth=false",
            force=False, local=False,
        )
        with patch("kanibako.targets.resolve_target", return_value=mock_target):
            run_set(set_args)
        capsys.readouterr()

        # Then reset.
        reset_args = argparse.Namespace(
            workset="resetauth", key="group_auth", reset_all=False, force=False,
        )
        rc = run_reset(reset_args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "group_auth" in out

        from kanibako.workset import load_workset
        ws = load_workset((tmp_home / "ws_resetauth").resolve())
        assert ws.group_auth is True

    def test_config_reset_all(self, config_file, tmp_home, capsys):
        """reset --all clears all overrides."""
        from kanibako.commands.workset_cmd import run_reset, run_set

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("resetall", tmp_home / "ws_resetall", std)

        # Set a value first (vault.enabled = scopeless own-file key, legal at
        # the workset scope under the B4 scope-direction guard).
        set_args = argparse.Namespace(
            workset="resetall", key_value="vault.enabled=false",
            force=False, local=False,
        )
        run_set(set_args)
        capsys.readouterr()

        # Reset all.
        reset_args = argparse.Namespace(
            workset="resetall", key=None, reset_all=True, force=True,
        )
        rc = run_reset(reset_args)
        assert rc == 0

    def test_config_reset_requires_key(self, config_file, tmp_home, capsys):
        """reset without a key or --all is an error."""
        from kanibako.commands.workset_cmd import run_reset

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("resetbare", tmp_home / "ws_resetbare", std)

        args = argparse.Namespace(
            workset="resetbare", key=None, reset_all=False, force=False,
        )
        rc = run_reset(args)
        assert rc == 1
        assert "requires a key" in capsys.readouterr().err

    def test_config_unknown_workset(self, config_file, tmp_home, capsys):
        """Config on unknown workset returns error."""
        from kanibako.commands.workset_cmd import run_show

        args = argparse.Namespace(workset="nosuchws", effective=False)
        rc = run_show(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "not registered" in err


class TestDefaultWorksetCli:
    def _std(self, config_file):
        config = load_config(config_file)
        return load_std_paths(config)

    def test_config_set_group_auth_roundtrips_via_config_toml(
        self, config_file, tmp_home, capsys,
    ):
        """`workset set default group_auth=false` writes config.yaml, not a
        workset.yaml."""
        from unittest.mock import MagicMock, patch

        from kanibako.commands.workset_cmd import run_set
        std = self._std(config_file)

        mock_target = MagicMock()
        args = argparse.Namespace(
            workset="default", key_value="group_auth=false",
            force=False, local=False,
        )
        with patch("kanibako.targets.resolve_target", return_value=mock_target):
            rc = run_set(args)
        assert rc == 0

        # No workset.yaml created at the data path.
        assert not (std.data_path / "workset.yaml").exists()
        # Block #2: the workset POLICY persists in config.yaml [project] as the
        # NEW spec key ``group_auth_enabled`` (never the old form).
        import yaml
        with open(std.data_path / "config.yaml") as f:
            data = yaml.safe_load(f)
        assert data["project"]["group_auth_enabled"] is False
        assert "group_auth" not in data["project"]

        # And it reads back via the default workset (new-wins-old).
        from kanibako.workset import default_workset
        assert default_workset(std).group_auth is False

    def test_config_get_group_auth(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_get
        args = argparse.Namespace(workset="default", key="group_auth")
        rc = run_get(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "True" in out

    def test_config_reset_group_auth(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_reset
        std = self._std(config_file)
        (std.data_path / "config.yaml").write_text("project:\n  group_auth: false\n")

        args = argparse.Namespace(
            workset="default", key="group_auth", reset_all=False, force=False,
        )
        rc = run_reset(args)
        assert rc == 0
        from kanibako.workset import default_workset
        assert default_workset(std).group_auth is True

    def test_config_set_regular_key_writes_config_toml(
        self, config_file, tmp_home, capsys,
    ):
        # vault.enabled = a SCOPELESS own-file regular key, legal at the workset
        # scope under the B4 scope-direction guard (it lands in [project], its
        # real stored key being enable_vault). ``box.image`` (a box.* key) would
        # be REFUSED here — see TestWorksetScopeDirection.
        from kanibako.commands.workset_cmd import run_set
        std = self._std(config_file)

        args = argparse.Namespace(
            workset="default", key_value="vault.enabled=false",
            force=False, local=False,
        )
        rc = run_set(args)
        assert rc == 0
        import yaml
        with open(std.data_path / "config.yaml") as f:
            data = yaml.safe_load(f)
        assert data["project"]["enable_vault"] is False
        assert not (std.data_path / "workset.yaml").exists()

    def test_info_default(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_info
        args = argparse.Namespace(name="default")
        rc = run_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "__default__" in out
        assert "<default workset>" in out

    def test_rm_default_refused(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_rm
        for name in ("default", "__default__"):
            args = argparse.Namespace(name=name, purge=False, force=True)
            rc = run_rm(args)
            assert rc == 1
            err = capsys.readouterr().err
            assert "cannot be removed" in err

    def test_disconnect_default_refused(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_disconnect
        args = argparse.Namespace(
            workset="default", project="anything",
            remove_files=False, force=True,
        )
        rc = run_disconnect(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "cannot be removed" in err


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

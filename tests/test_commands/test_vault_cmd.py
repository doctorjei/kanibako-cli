"""Tests for kanibako vault CLI commands (nested under box vault)."""

from __future__ import annotations



from kanibako.cli import build_parser
from kanibako.commands.vault_cmd import (
    run_list,
    run_prune,
    run_restore,
    run_snapshot,
)
from kanibako.settings.config import load_config
from kanibako.settings.paths import load_std_paths, resolve_project


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_project_with_vault(config_file, tmp_home, credentials_dir):
    """Initialize a default-mode project and populate vault share-rw."""
    config = load_config(config_file)
    std = load_std_paths(config)
    project_dir = str(tmp_home / "project")
    proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

    # Populate share-rw with test data.
    (proj.vault_rw_path / "data.txt").write_text("hello vault")
    return proj


# ---------------------------------------------------------------------------
# Parser tests — vault is now a box subcommand
# ---------------------------------------------------------------------------


class TestVaultParser:
    def test_vault_subcommand_recognized(self):
        parser = build_parser()
        args = parser.parse_args(["box", "vault", "list"])
        assert args.command == "box"
        assert args.vault_command == "list"

    def test_vault_snapshot_parser(self):
        parser = build_parser()
        args = parser.parse_args(["box", "vault", "snapshot", "/foo"])
        assert args.vault_command == "snapshot"
        assert args.project == "/foo"

    def test_vault_restore_parser(self):
        parser = build_parser()
        args = parser.parse_args(["box", "vault", "restore", "my-snap.tar.xz"])
        assert args.vault_command == "restore"
        assert args.name == "my-snap.tar.xz"

    def test_vault_prune_parser(self):
        parser = build_parser()
        args = parser.parse_args(["box", "vault", "prune", "--keep", "3"])
        assert args.vault_command == "prune"
        assert args.keep == 3

    def test_vault_list_quiet_flag(self):
        parser = build_parser()
        args = parser.parse_args(["box", "vault", "list", "-q"])
        assert args.vault_command == "list"
        assert args.quiet is True

    def test_vault_restore_force_flag(self):
        parser = build_parser()
        args = parser.parse_args(["box", "vault", "restore", "snap.tar.xz", "--force"])
        assert args.vault_command == "restore"
        assert args.force is True

    def test_vault_prune_force_flag(self):
        parser = build_parser()
        args = parser.parse_args(["box", "vault", "prune", "--force"])
        assert args.vault_command == "prune"
        assert args.force is True


# ---------------------------------------------------------------------------
# Snapshot command
# ---------------------------------------------------------------------------


class TestVaultSnapshot:
    def test_snapshot_creates_archive(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        proj = _init_project_with_vault(config_file, tmp_home, credentials_dir)
        parser = build_parser()
        args = parser.parse_args(["box", "vault", "snapshot", str(proj.project_path)])
        rc = run_snapshot(args)

        assert rc == 0
        captured = capsys.readouterr()
        assert "Snapshot created" in captured.out

    def test_snapshot_empty_vault(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        parser = build_parser()
        args = parser.parse_args(["box", "vault", "snapshot", str(proj.project_path)])
        rc = run_snapshot(args)

        assert rc == 0
        captured = capsys.readouterr()
        assert "empty" in captured.err.lower() or "Nothing" in captured.err


# ---------------------------------------------------------------------------
# List command
# ---------------------------------------------------------------------------


class TestVaultList:
    def test_list_empty(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        parser = build_parser()
        args = parser.parse_args(["box", "vault", "list", str(tmp_home / "project")])
        rc = run_list(args)

        assert rc == 0
        captured = capsys.readouterr()
        assert "No snapshots" in captured.out

    def test_list_after_snapshot(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        proj = _init_project_with_vault(config_file, tmp_home, credentials_dir)

        from kanibako.snapshots import create_snapshot
        create_snapshot(proj.vault_rw_path)

        parser = build_parser()
        args = parser.parse_args(["box", "vault", "list", str(proj.project_path)])
        rc = run_list(args)

        assert rc == 0
        captured = capsys.readouterr()
        # Directory snapshot name is a bare UTC timestamp.
        assert "Z" in captured.out
        assert "UTC" in captured.out

    def test_list_quiet(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        proj = _init_project_with_vault(config_file, tmp_home, credentials_dir)

        from kanibako.snapshots import create_snapshot
        snap = create_snapshot(proj.vault_rw_path)

        parser = build_parser()
        args = parser.parse_args(["box", "vault", "list", "-q", str(proj.project_path)])
        rc = run_list(args)

        assert rc == 0
        captured = capsys.readouterr()
        # Quiet mode: just snapshot names, no timestamps or sizes
        assert snap.name in captured.out
        assert "UTC" not in captured.out


# ---------------------------------------------------------------------------
# Restore command
# ---------------------------------------------------------------------------


class TestVaultRestore:
    def test_restore_success(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        proj = _init_project_with_vault(config_file, tmp_home, credentials_dir)

        from kanibako.snapshots import create_snapshot
        snap = create_snapshot(proj.vault_rw_path)

        # Modify data
        (proj.vault_rw_path / "data.txt").write_text("modified")

        parser = build_parser()
        args = parser.parse_args([
            "box", "vault", "restore", snap.name, str(proj.project_path), "--force",
        ])
        rc = run_restore(args)

        assert rc == 0
        assert (proj.vault_rw_path / "data.txt").read_text() == "hello vault"

    def test_restore_missing_snapshot(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        proj = _init_project_with_vault(config_file, tmp_home, credentials_dir)

        parser = build_parser()
        args = parser.parse_args([
            "box", "vault", "restore", "nonexistent.tar.xz", str(proj.project_path),
        ])
        rc = run_restore(args)

        assert rc == 1
        captured = capsys.readouterr()
        assert "not found" in captured.err.lower()


# ---------------------------------------------------------------------------
# Prune command
# ---------------------------------------------------------------------------


class TestVaultPrune:
    def test_prune_nothing(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        proj = _init_project_with_vault(config_file, tmp_home, credentials_dir)

        parser = build_parser()
        args = parser.parse_args(["box", "vault", "prune", str(proj.project_path)])
        rc = run_prune(args)

        assert rc == 0
        captured = capsys.readouterr()
        assert "Nothing to prune" in captured.out

    def test_prune_removes_old(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        proj = _init_project_with_vault(config_file, tmp_home, credentials_dir)

        # Create multiple directory snapshots
        import shutil
        versions = proj.vault_rw_path.parent / ".versions"
        versions.mkdir(parents=True, exist_ok=True)
        for i in range(5):
            name = f"2026010{i + 1}T000000Z"
            snap_dir = versions / name
            snap_dir.mkdir()
            shutil.copy2(
                proj.vault_rw_path / "data.txt", snap_dir / "data.txt",
            )

        parser = build_parser()
        args = parser.parse_args([
            "box", "vault", "prune", "--keep", "2", str(proj.project_path), "--force",
        ])
        rc = run_prune(args)

        assert rc == 0
        captured = capsys.readouterr()
        assert "Pruned 3" in captured.out


# ---------------------------------------------------------------------------
# Destructive commands confirm unless --force
# ---------------------------------------------------------------------------


def _no_stdin(*_args, **_kwargs):
    raise AssertionError("stdin was read; this command must not prompt")


def _make_snapshots(proj, count: int = 5) -> None:
    import shutil
    versions = proj.vault_rw_path.parent / ".versions"
    versions.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        snap_dir = versions / f"2026010{i + 1}T000000Z"
        snap_dir.mkdir()
        shutil.copy2(proj.vault_rw_path / "data.txt", snap_dir / "data.txt")


class TestVaultRestoreConfirms:
    """``--force`` is the only thing that skips the prompt; a declined or closed
    stdin leaves share-rw exactly as it was."""

    def test_declined_prompt_restores_nothing(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        proj = _init_project_with_vault(config_file, tmp_home, credentials_dir)

        from kanibako.snapshots import create_snapshot
        snap = create_snapshot(proj.vault_rw_path)
        (proj.vault_rw_path / "data.txt").write_text("modified")

        monkeypatch.setattr("builtins.input", lambda *_a: "no")
        parser = build_parser()
        args = parser.parse_args([
            "box", "vault", "restore", snap.name, str(proj.project_path),
        ])
        rc = run_restore(args)

        assert rc == 2
        assert (proj.vault_rw_path / "data.txt").read_text() == "modified"
        assert "Aborted." in capsys.readouterr().out

    def test_closed_stdin_restores_nothing(
        self, config_file, tmp_home, credentials_dir, monkeypatch,
    ):
        proj = _init_project_with_vault(config_file, tmp_home, credentials_dir)

        from kanibako.snapshots import create_snapshot
        snap = create_snapshot(proj.vault_rw_path)
        (proj.vault_rw_path / "data.txt").write_text("modified")

        def _eof(*_a):
            raise EOFError

        monkeypatch.setattr("builtins.input", _eof)
        parser = build_parser()
        args = parser.parse_args([
            "box", "vault", "restore", snap.name, str(proj.project_path),
        ])
        rc = run_restore(args)

        assert rc == 2
        assert (proj.vault_rw_path / "data.txt").read_text() == "modified"

    def test_confirmed_prompt_restores(
        self, config_file, tmp_home, credentials_dir, monkeypatch,
    ):
        proj = _init_project_with_vault(config_file, tmp_home, credentials_dir)

        from kanibako.snapshots import create_snapshot
        snap = create_snapshot(proj.vault_rw_path)
        (proj.vault_rw_path / "data.txt").write_text("modified")

        monkeypatch.setattr("builtins.input", lambda *_a: "yes")
        parser = build_parser()
        args = parser.parse_args([
            "box", "vault", "restore", snap.name, str(proj.project_path),
        ])
        rc = run_restore(args)

        assert rc == 0
        assert (proj.vault_rw_path / "data.txt").read_text() == "hello vault"

    def test_force_skips_prompt(self, config_file, tmp_home, credentials_dir,
                                monkeypatch):
        proj = _init_project_with_vault(config_file, tmp_home, credentials_dir)

        from kanibako.snapshots import create_snapshot
        snap = create_snapshot(proj.vault_rw_path)
        (proj.vault_rw_path / "data.txt").write_text("modified")

        monkeypatch.setattr("builtins.input", _no_stdin)
        parser = build_parser()
        args = parser.parse_args([
            "box", "vault", "restore", snap.name, str(proj.project_path), "--force",
        ])
        rc = run_restore(args)

        assert rc == 0
        assert (proj.vault_rw_path / "data.txt").read_text() == "hello vault"

    def test_unknown_name_does_not_prompt(
        self, config_file, tmp_home, credentials_dir, monkeypatch,
    ):
        proj = _init_project_with_vault(config_file, tmp_home, credentials_dir)

        monkeypatch.setattr("builtins.input", _no_stdin)
        parser = build_parser()
        args = parser.parse_args([
            "box", "vault", "restore", "nonexistent", str(proj.project_path),
        ])
        rc = run_restore(args)

        assert rc == 1
        assert (proj.vault_rw_path / "data.txt").read_text() == "hello vault"


class TestVaultPruneConfirms:
    """A declined prune keeps every snapshot it would otherwise have deleted."""

    def test_declined_prompt_prunes_nothing(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        proj = _init_project_with_vault(config_file, tmp_home, credentials_dir)
        _make_snapshots(proj)
        versions = proj.vault_rw_path.parent / ".versions"

        monkeypatch.setattr("builtins.input", lambda *_a: "no")
        parser = build_parser()
        args = parser.parse_args([
            "box", "vault", "prune", "--keep", "2", str(proj.project_path),
        ])
        rc = run_prune(args)

        assert rc == 2
        assert len(list(versions.iterdir())) == 5
        assert "Aborted." in capsys.readouterr().out

    def test_confirmed_prompt_prunes(
        self, config_file, tmp_home, credentials_dir, monkeypatch,
    ):
        proj = _init_project_with_vault(config_file, tmp_home, credentials_dir)
        _make_snapshots(proj)
        versions = proj.vault_rw_path.parent / ".versions"

        monkeypatch.setattr("builtins.input", lambda *_a: "yes")
        parser = build_parser()
        args = parser.parse_args([
            "box", "vault", "prune", "--keep", "2", str(proj.project_path),
        ])
        rc = run_prune(args)

        assert rc == 0
        assert len(list(versions.iterdir())) == 2

    def test_force_skips_prompt(self, config_file, tmp_home, credentials_dir,
                                monkeypatch):
        proj = _init_project_with_vault(config_file, tmp_home, credentials_dir)
        _make_snapshots(proj)
        versions = proj.vault_rw_path.parent / ".versions"

        monkeypatch.setattr("builtins.input", _no_stdin)
        parser = build_parser()
        args = parser.parse_args([
            "box", "vault", "prune", "--keep", "2", str(proj.project_path), "--force",
        ])
        rc = run_prune(args)

        assert rc == 0
        assert len(list(versions.iterdir())) == 2

    def test_nothing_to_prune_does_not_prompt(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        proj = _init_project_with_vault(config_file, tmp_home, credentials_dir)
        _make_snapshots(proj, count=1)

        monkeypatch.setattr("builtins.input", _no_stdin)
        parser = build_parser()
        args = parser.parse_args([
            "box", "vault", "prune", "--keep", "2", str(proj.project_path),
        ])
        rc = run_prune(args)

        assert rc == 0
        assert "Nothing to prune" in capsys.readouterr().out


class TestVaultForceDoesNotOverrideRefusals:
    """``--force`` skips the prompt only; every pre-existing refusal still holds."""

    def _disabled_project(self, config_file, tmp_home):
        config = load_config(config_file)
        std = load_std_paths(config)
        resolve_project(
            std, config, project_dir=str(tmp_home / "project"),
            initialize=True, enable_vault=False,
        )

    def test_restore_refuses_disabled_vault(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        self._disabled_project(config_file, tmp_home)
        monkeypatch.setattr("builtins.input", _no_stdin)

        parser = build_parser()
        args = parser.parse_args([
            "box", "vault", "restore", "20260101T000000Z",
            str(tmp_home / "project"), "--force",
        ])
        rc = run_restore(args)

        assert rc == 1
        assert "disabled" in capsys.readouterr().err.lower()

    def test_prune_refuses_disabled_vault(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        self._disabled_project(config_file, tmp_home)
        monkeypatch.setattr("builtins.input", _no_stdin)

        parser = build_parser()
        args = parser.parse_args([
            "box", "vault", "prune", str(tmp_home / "project"), "--force",
        ])
        rc = run_prune(args)

        assert rc == 1
        assert "disabled" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------------------
# Vault disabled
# ---------------------------------------------------------------------------


class TestVaultDisabled:
    def test_vault_disabled_returns_error(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(
            std, config, project_dir=project_dir,
            initialize=True, enable_vault=False,
        )

        parser = build_parser()
        args = parser.parse_args(["box", "vault", "list", str(tmp_home / "project")])
        rc = run_list(args)

        assert rc == 1
        captured = capsys.readouterr()
        assert "disabled" in captured.err.lower()

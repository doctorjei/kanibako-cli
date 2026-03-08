"""Tests for kanibako vault CLI commands (nested under box vault)."""

from __future__ import annotations



from kanibako.cli import build_parser
from kanibako.commands.vault_cmd import (
    run_list,
    run_prune,
    run_restore,
    run_snapshot,
)
from kanibako.config import load_config
from kanibako.paths import load_std_paths, resolve_project


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_project_with_vault(config_file, tmp_home, credentials_dir):
    """Initialize a local project and populate vault share-rw."""
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
        assert ".tar.xz" in captured.out
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
            "box", "vault", "restore", snap.name, str(proj.project_path),
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

        # Create multiple snapshots
        import tarfile
        versions = proj.vault_rw_path.parent / ".versions"
        versions.mkdir(parents=True, exist_ok=True)
        for i in range(5):
            name = f"2026010{i + 1}T000000Z.tar.xz"
            with tarfile.open(versions / name, "w:xz") as tar:
                tar.add(str(proj.vault_rw_path / "data.txt"), arcname="data.txt")

        parser = build_parser()
        args = parser.parse_args([
            "box", "vault", "prune", "--keep", "2", str(proj.project_path),
        ])
        rc = run_prune(args)

        assert rc == 0
        captured = capsys.readouterr()
        assert "Pruned 3" in captured.out


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
            initialize=True, vault_enabled=False,
        )

        parser = build_parser()
        args = parser.parse_args(["box", "vault", "list", str(tmp_home / "project")])
        rc = run_list(args)

        assert rc == 1
        captured = capsys.readouterr()
        assert "disabled" in captured.err.lower()

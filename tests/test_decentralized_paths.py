"""Tests for resolve_decentralized_project() in kanibako.paths."""

from __future__ import annotations

import json

import pytest

from kanibako.errors import ProjectError
from kanibako.paths import (
    ProjectMode,
    resolve_decentralized_project,
)
from kanibako.utils import project_hash


# ---------------------------------------------------------------------------
# TestResolveDecentralizedProject
# ---------------------------------------------------------------------------

class TestResolveDecentralizedProject:
    def test_returns_decentralized_mode(self, std, config, project_dir):
        proj = resolve_decentralized_project(
            std, config, str(project_dir), initialize=True,
        )
        assert proj.mode is ProjectMode.decentralized

    def test_paths_are_inside_project_dir(self, std, config, project_dir):
        proj = resolve_decentralized_project(
            std, config, str(project_dir), initialize=True,
        )
        resolved = project_dir.resolve()
        assert proj.project_path == resolved
        assert proj.metadata_path == resolved / ".kanibako"
        assert proj.shell_path == resolved / ".kanibako" / "shell"
        assert proj.vault_ro_path == resolved / "vault" / "share-ro"
        assert proj.vault_rw_path == resolved / "vault" / "share-rw"

    def test_project_hash_is_sha256_of_resolved_path(
        self, std, config, project_dir,
    ):
        proj = resolve_decentralized_project(std, config, str(project_dir))
        expected = project_hash(str(project_dir.resolve()))
        assert proj.project_hash == expected

    def test_nonexistent_path_raises(self, std, config, tmp_home):
        missing = tmp_home / "does-not-exist"
        with pytest.raises(ProjectError, match="does not exist"):
            resolve_decentralized_project(std, config, str(missing))

    def test_defaults_to_cwd(self, std, config, project_dir, monkeypatch):
        monkeypatch.chdir(project_dir)
        proj = resolve_decentralized_project(std, config, project_dir=None)
        assert proj.project_path == project_dir.resolve()

    def test_initialize_creates_metadata_and_home(
        self, std, config, project_dir, credentials_dir,
    ):
        proj = resolve_decentralized_project(
            std, config, str(project_dir), initialize=True,
        )
        assert proj.metadata_path.is_dir()
        assert proj.shell_path.is_dir()

    def test_initialize_does_not_copy_credentials(
        self, std, config, project_dir, credentials_dir,
    ):
        """Credential copy is now handled by target.init_home(), not during init."""
        proj = resolve_decentralized_project(
            std, config, str(project_dir), initialize=True,
        )
        creds_file = proj.shell_path / ".claude" / ".credentials.json"
        assert not creds_file.exists()

    def test_initialize_bootstraps_shell(
        self, std, config, project_dir, credentials_dir,
    ):
        proj = resolve_decentralized_project(
            std, config, str(project_dir), initialize=True,
        )
        assert (proj.shell_path / ".bashrc").is_file()
        assert (proj.shell_path / ".profile").is_file()

    def test_initialize_creates_vault_dirs(
        self, std, config, project_dir, credentials_dir,
    ):
        proj = resolve_decentralized_project(
            std, config, str(project_dir), initialize=True,
        )
        assert proj.vault_ro_path.is_dir()
        assert proj.vault_rw_path.is_dir()

    def test_initialize_creates_vault_gitignore(
        self, std, config, project_dir, credentials_dir,
    ):
        resolve_decentralized_project(
            std, config, str(project_dir), initialize=True,
        )
        gitignore = project_dir.resolve() / "vault" / ".gitignore"
        assert gitignore.is_file()
        assert "share-rw/" in gitignore.read_text()

    def test_no_initialize_skips_creation(self, std, config, project_dir):
        proj = resolve_decentralized_project(
            std, config, str(project_dir), initialize=False,
        )
        assert not proj.metadata_path.is_dir()
        assert not proj.is_new

    def test_is_new_true_on_first_init(
        self, std, config, project_dir, credentials_dir,
    ):
        proj = resolve_decentralized_project(
            std, config, str(project_dir), initialize=True,
        )
        assert proj.is_new is True

    def test_is_new_false_on_reinit(
        self, std, config, project_dir, credentials_dir,
    ):
        resolve_decentralized_project(
            std, config, str(project_dir), initialize=True,
        )
        proj2 = resolve_decentralized_project(
            std, config, str(project_dir), initialize=True,
        )
        assert proj2.is_new is False

    def test_no_breadcrumb(
        self, std, config, project_dir, credentials_dir,
    ):
        """Decentralized projects should NOT create project-path.txt."""
        proj = resolve_decentralized_project(
            std, config, str(project_dir), initialize=True,
        )
        assert not (proj.metadata_path / "project-path.txt").exists()

    def test_recovery_missing_shell_path(
        self, std, config, project_dir, credentials_dir,
    ):
        proj = resolve_decentralized_project(
            std, config, str(project_dir), initialize=True,
        )
        import shutil
        shutil.rmtree(proj.shell_path)
        assert not proj.shell_path.exists()

        proj2 = resolve_decentralized_project(
            std, config, str(project_dir), initialize=True,
        )
        assert proj2.shell_path.is_dir()
        assert (proj2.shell_path / ".bashrc").is_file()
        assert (proj2.shell_path / ".profile").is_file()


# ---------------------------------------------------------------------------
# TestDecentralizedCredentialFlow
# ---------------------------------------------------------------------------

class TestDecentralizedGlobalSharedPath:
    def test_decentralized_has_no_global_shared_path(
        self, std, config, project_dir, credentials_dir,
    ):
        """Decentralized projects don't get shared caches."""
        proj = resolve_decentralized_project(
            std, config, str(project_dir), initialize=True,
        )
        assert proj.global_shared_path is None


class TestDecentralizedLocalSharedPath:
    def test_decentralized_has_no_local_shared_path(
        self, std, config, project_dir, credentials_dir,
    ):
        """Decentralized projects don't get agent-level shared caches."""
        proj = resolve_decentralized_project(
            std, config, str(project_dir), initialize=True,
        )
        assert proj.local_shared_path is None


class TestDecentralizedCredentialFlow:
    def test_no_credentials_during_init(
        self, std, config, project_dir, credentials_dir,
    ):
        """Init no longer copies credentials; that's target.init_home()'s job."""
        proj = resolve_decentralized_project(
            std, config, str(project_dir), initialize=True,
        )
        creds_file = proj.shell_path / ".claude" / ".credentials.json"
        assert not creds_file.exists()

    def test_refresh_host_to_project_works(
        self, std, config, project_dir, credentials_dir, tmp_home,
    ):
        proj = resolve_decentralized_project(
            std, config, str(project_dir), initialize=True,
        )
        from kanibako_plugin_claude.credentials import refresh_host_to_project

        home = tmp_home / "home"
        host_creds = home / ".claude" / ".credentials.json"

        # Create the .claude dir and seed a creds file so refresh can write to it
        claude_dir = proj.shell_path / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        project_creds = claude_dir / ".credentials.json"
        project_creds.write_text(json.dumps({"claudeAiOauth": {"token": "old"}}))

        # Touch host to ensure it's newer.
        import time
        time.sleep(0.05)
        host_creds.write_text(json.dumps(
            {"claudeAiOauth": {"token": "refreshed-token"}}
        ))

        result = refresh_host_to_project(host_creds, project_creds)
        assert result is True

        updated = json.loads(project_creds.read_text())
        assert updated["claudeAiOauth"]["token"] == "refreshed-token"

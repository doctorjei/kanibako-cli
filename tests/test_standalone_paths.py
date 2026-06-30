"""Tests for resolve_standalone_project() in kanibako.paths."""

from __future__ import annotations

import json

import pytest

from kanibako import registry_store
from kanibako.errors import ProjectError
from kanibako.paths import (
    BoxMode,
    detect_project_mode,
    helper_log_path,
    resolve_standalone_project,
)
from kanibako.utils import project_hash


# ---------------------------------------------------------------------------
# TestResolveStandaloneProject
# ---------------------------------------------------------------------------

class TestResolveStandaloneProject:
    def test_returns_standalone_mode(self, std, config, project_dir):
        proj = resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        assert proj.mode is BoxMode.standalone

    def test_paths_are_inside_project_dir(self, std, config, project_dir):
        proj = resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        resolved = project_dir.resolve()
        # Drift H: workspace is the <root>/workspace SUBDIR (mount source).
        assert proj.project_path == resolved / "workspace"
        # Drift I: metadata (settings.yaml) is at the ROOT.
        assert proj.metadata_path == resolved
        assert proj.shell_path == resolved / "box_data" / "home"
        assert proj.vault_ro_path == resolved / "vault" / "ro"
        assert proj.vault_rw_path == resolved / "vault" / "rw"

    def test_project_hash_is_sha256_of_resolved_path(
        self, std, config, project_dir,
    ):
        proj = resolve_standalone_project(std, config, str(project_dir))
        expected = project_hash(str(project_dir.resolve()))
        assert proj.project_hash == expected

    def test_nonexistent_path_raises(self, std, config, tmp_home):
        missing = tmp_home / "does-not-exist"
        with pytest.raises(ProjectError, match="does not exist"):
            resolve_standalone_project(std, config, str(missing))

    def test_defaults_to_cwd(self, std, config, project_dir, monkeypatch):
        monkeypatch.chdir(project_dir)
        proj = resolve_standalone_project(std, config, project_dir=None)
        # The workspace is the <root>/workspace subdir; the root is cwd.
        assert proj.project_path == project_dir.resolve() / "workspace"
        assert proj.metadata_path == project_dir.resolve()

    def test_initialize_creates_metadata_and_home(
        self, std, config, project_dir, credentials_dir,
    ):
        proj = resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        assert proj.metadata_path.is_dir()
        assert proj.shell_path.is_dir()

    def test_initialize_does_not_copy_credentials(
        self, std, config, project_dir, credentials_dir,
    ):
        """Credential copy is now handled by target.init_home(), not during init."""
        proj = resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        creds_file = proj.shell_path / ".claude" / ".credentials.json"
        assert not creds_file.exists()

    def test_initialize_bootstraps_shell(
        self, std, config, project_dir, credentials_dir,
    ):
        proj = resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        assert (proj.shell_path / ".bashrc").is_file()
        assert (proj.shell_path / ".profile").is_file()

    def test_initialize_creates_vault_dirs(
        self, std, config, project_dir, credentials_dir,
    ):
        proj = resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        assert proj.vault_ro_path.is_dir()
        assert proj.vault_rw_path.is_dir()

    def test_initialize_creates_vault_gitignore(
        self, std, config, project_dir, credentials_dir,
    ):
        resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        gitignore = project_dir.resolve() / "vault" / ".gitignore"
        assert gitignore.is_file()
        assert "rw/" in gitignore.read_text()

    def test_no_initialize_skips_creation(self, std, config, project_dir):
        proj = resolve_standalone_project(
            std, config, str(project_dir), initialize=False,
        )
        # metadata_path is the root (always exists); the box_data/ marker dir is
        # what signals an initialized standalone box, and it must be absent.
        assert not (proj.metadata_path / "box_data").is_dir()
        assert not proj.is_new

    def test_is_new_true_on_first_init(
        self, std, config, project_dir, credentials_dir,
    ):
        proj = resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        assert proj.is_new is True

    def test_is_new_false_on_reinit(
        self, std, config, project_dir, credentials_dir,
    ):
        resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        proj2 = resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        assert proj2.is_new is False

    def test_no_breadcrumb(
        self, std, config, project_dir, credentials_dir,
    ):
        """Standalone projects should NOT create project-path.txt."""
        proj = resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        assert not (proj.metadata_path / "project-path.txt").exists()

    def test_recovery_missing_shell_path(
        self, std, config, project_dir, credentials_dir,
    ):
        proj = resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        import shutil
        shutil.rmtree(proj.shell_path)
        assert not proj.shell_path.exists()

        proj2 = resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        assert proj2.shell_path.is_dir()
        assert (proj2.shell_path / ".bashrc").is_file()
        assert (proj2.shell_path / ".profile").is_file()


# ---------------------------------------------------------------------------
# TestStandaloneCredentialFlow
# ---------------------------------------------------------------------------

# The global/local shared-path fields were removed in 1.6.0 (Part 4): no
# ``shared/`` dir exists in the target tree.  The no-shared-path assertions
# (which only confirmed the now-deleted fields were None) are removed.


class TestStandaloneCredentialFlow:
    def test_no_credentials_during_init(
        self, std, config, project_dir, credentials_dir,
    ):
        """Init no longer copies credentials; that's target.init_home()'s job."""
        proj = resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        creds_file = proj.shell_path / ".claude" / ".credentials.json"
        assert not creds_file.exists()

    def test_refresh_host_to_project_works(
        self, std, config, project_dir, credentials_dir, tmp_home,
    ):
        proj = resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        from kanibako.plugins.claude.credentials import refresh_host_to_project

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


# ---------------------------------------------------------------------------
# Characterization: standalone fixed path table (Phase 5, no layout axis)
# ---------------------------------------------------------------------------

class TestStandaloneFixedPaths:
    """Pin the concrete paths the STANDALONE fixed table produces.

    Drift H+I: the box ``settings.yaml`` lives at the ROOT (``metadata_path`` is
    the root), the live workspace is the ``<root>/workspace`` subdir, the agent
    home is ``box_data/home`` (the ``box_data/`` marker dir also holds the
    helper log), and the vault lives at ``<root>/vault/{ro,rw}``.
    """

    def test_standalone_paths(self, std, config, project_dir, credentials_dir):
        proj = resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        resolved = project_dir.resolve()

        assert proj.metadata_path == resolved
        assert proj.project_path == resolved / "workspace"
        assert proj.shell_path == resolved / "box_data" / "home"
        assert proj.vault_ro_path == resolved / "vault" / "ro"
        assert proj.vault_rw_path == resolved / "vault" / "rw"
        # Settings at root; box_data holds home + (after launch) the helper log.
        assert (resolved / "settings.yaml").is_file()
        assert (resolved / "box_data").is_dir()
        assert not (resolved / "box_data" / "settings.yaml").exists()

    def test_helper_log_stays_in_box_data(
        self, std, config, project_dir, credentials_dir,
    ):
        """The helper log lives INSIDE box_data/ (drift critical interaction):
        settings moved to the root, but the <box>.jsonl log is anchored under
        box_data/ so the whole standalone tree stays drop-in portable."""
        proj = resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        resolved = project_dir.resolve()
        log = helper_log_path(std, proj)
        assert log == resolved / "box_data" / f"{proj.name}.jsonl"


# ---------------------------------------------------------------------------
# TestStandaloneIdentity (5d: <random24>_<leaf> + registry.standalone)
# ---------------------------------------------------------------------------

class TestStandaloneIdentity:
    def test_create_assigns_random_leaf_name(
        self, std, config, project_dir, credentials_dir,
    ):
        proj = resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        # name = <random24>_<leaf>; leaf is the project dir basename.
        assert proj.name
        prefix, _, leaf = proj.name.partition("_")
        assert leaf == project_dir.resolve().name
        assert len(prefix) == 5  # 24 bits → 5 base32 chars

    def test_create_registers_in_standalone_section(
        self, std, config, project_dir, credentials_dir,
    ):
        proj = resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        standalone = registry_store.load_standalone(std.registry)
        assert standalone.get(proj.name) == str(project_dir.resolve())

    def test_reinit_reuses_stored_name(
        self, std, config, project_dir, credentials_dir,
    ):
        proj1 = resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        proj2 = resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        assert proj2.name == proj1.name

    def test_resolve_without_meta_has_empty_name(
        self, std, config, project_dir,
    ):
        # No initialize, no on-disk meta → no identity yet.
        proj = resolve_standalone_project(
            std, config, str(project_dir), initialize=False,
        )
        assert proj.name == ""


# ---------------------------------------------------------------------------
# TestStandaloneAtomicCreate (BUG-A: name-collision pre-flight before FS init)
# ---------------------------------------------------------------------------

class TestStandaloneAtomicCreate:
    def test_taken_canonical_name_refuses_before_fs_init(
        self, std, config, project_dir, credentials_dir,
    ):
        """A create whose --name is a TAKEN verbatim canonical id refuses up
        front, leaving NO half-created box_data/ or vault/ tree (BUG-A)."""
        # Register a canonical id so the requested --name collides.
        taken = "abcde_taken"
        registry_store.register_standalone(std.registry, taken, project_dir)

        with pytest.raises(ProjectError):
            resolve_standalone_project(
                std, config, str(project_dir), initialize=True, name=taken,
            )

        # No orphaned kanibako-managed tree left behind.
        assert not (project_dir / "box_data").exists()
        assert not (project_dir / "vault").exists()

    def test_free_canonical_name_still_creates(
        self, std, config, project_dir, credentials_dir,
    ):
        """A free canonical --name is honored verbatim (no false refusal)."""
        proj = resolve_standalone_project(
            std, config, str(project_dir), initialize=True, name="abcde_mine",
        )
        assert proj.name == "abcde_mine"
        assert (project_dir / "box_data").is_dir()


# ---------------------------------------------------------------------------
# TestStandaloneDetection (5d: box_data/ walk marker)
# ---------------------------------------------------------------------------

class TestStandaloneDetection:
    def test_detect_finds_box_data_marker(
        self, std, config, project_dir, credentials_dir,
    ):
        resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        result = detect_project_mode(project_dir, std, config)
        assert result.mode is BoxMode.standalone
        assert result.project_root == project_dir.resolve()

    def test_detect_from_subdir_walks_up_to_marker(
        self, std, config, project_dir, credentials_dir,
    ):
        resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        subdir = project_dir / "src" / "deep"
        subdir.mkdir(parents=True)
        result = detect_project_mode(subdir, std, config)
        assert result.mode is BoxMode.standalone
        assert result.project_root == project_dir.resolve()

    def test_bare_box_data_dir_is_not_a_marker(
        self, std, config, project_dir,
    ):
        # A box_data/ dir without a standalone metadata file must not detect.
        (project_dir / "box_data").mkdir()
        result = detect_project_mode(project_dir, std, config)
        assert result.mode is BoxMode.primary

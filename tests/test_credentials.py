"""Tests for Claude credential functions (kanibako_plugin_claude.credentials)."""

from __future__ import annotations

import json
import os
import time

from kanibako_plugin_claude import ClaudeTarget
from kanibako_plugin_claude.credentials import (
    filter_settings,
    refresh_host_to_project,
    writeback_project_to_host,
)


class TestRefreshHostToProject:
    def test_creates_project_from_host(self, tmp_path):
        host = tmp_path / "host" / ".credentials.json"
        host.parent.mkdir()
        host.write_text(json.dumps({"claudeAiOauth": {"token": "abc"}}))

        project = tmp_path / "project" / ".credentials.json"
        project.parent.mkdir()

        assert refresh_host_to_project(host, project)
        data = json.loads(project.read_text())
        assert data["claudeAiOauth"]["token"] == "abc"

    def test_merges_oauth_key(self, tmp_path):
        host = tmp_path / "host" / ".credentials.json"
        host.parent.mkdir()
        host.write_text(json.dumps({"claudeAiOauth": {"token": "new"}}))

        project = tmp_path / "project" / ".credentials.json"
        project.parent.mkdir()
        project.write_text(json.dumps({"claudeAiOauth": {"token": "old"}, "otherKey": True}))

        # Make host newer
        old_time = time.time() - 100
        os.utime(project, (old_time, old_time))

        assert refresh_host_to_project(host, project)
        data = json.loads(project.read_text())
        assert data["claudeAiOauth"]["token"] == "new"
        assert data["otherKey"] is True

    def test_skips_when_project_newer(self, tmp_path):
        host = tmp_path / "host" / ".credentials.json"
        host.parent.mkdir()
        host.write_text(json.dumps({"claudeAiOauth": {"token": "host"}}))

        project = tmp_path / "project" / ".credentials.json"
        project.parent.mkdir()
        project.write_text(json.dumps({"claudeAiOauth": {"token": "project"}}))

        # Make host older
        old_time = time.time() - 100
        os.utime(host, (old_time, old_time))

        assert not refresh_host_to_project(host, project)
        data = json.loads(project.read_text())
        assert data["claudeAiOauth"]["token"] == "project"

    def test_missing_oauth_key_skips(self, tmp_path):
        host = tmp_path / "host" / ".credentials.json"
        host.parent.mkdir()
        host.write_text(json.dumps({"otherKey": True}))

        project = tmp_path / "project" / ".credentials.json"
        project.parent.mkdir()
        project.write_text(json.dumps({"claudeAiOauth": {"token": "old"}}))

        # Make host newer
        old_time = time.time() - 100
        os.utime(project, (old_time, old_time))

        assert not refresh_host_to_project(host, project)


class TestFilterSettings:
    def test_filters_keys(self, tmp_path):
        src = tmp_path / "source.json"
        dst = tmp_path / "dest.json"

        src.write_text(json.dumps({
            "oauthAccount": "acct",
            "hasCompletedOnboarding": False,
            "installMethod": "cli",
            "extraKey": "ignored",
        }))

        filter_settings(src, dst)
        data = json.loads(dst.read_text())
        assert data["oauthAccount"] == "acct"
        assert data["hasCompletedOnboarding"] is True  # Always set to True
        assert data["installMethod"] == "cli"
        assert "extraKey" not in data


# ---------------------------------------------------------------------------
# refresh_host_to_project — error paths
# ---------------------------------------------------------------------------

class TestRefreshHostToProjectErrors:
    def test_malformed_host_json(self, tmp_path):
        host = tmp_path / "host" / ".credentials.json"
        host.parent.mkdir(parents=True)
        host.write_text("{bad json!!")

        project = tmp_path / "project" / ".credentials.json"
        project.parent.mkdir(parents=True)
        project.write_text(json.dumps({"claudeAiOauth": {"token": "old"}}))
        # Ensure host is newer
        os.utime(project, (0, 0))

        result = refresh_host_to_project(host, project)
        assert result is False
        # Project file unchanged
        data = json.loads(project.read_text())
        assert data["claudeAiOauth"]["token"] == "old"

    def test_empty_host_file(self, tmp_path):
        host = tmp_path / "host" / ".credentials.json"
        host.parent.mkdir(parents=True)
        host.write_text("")

        project = tmp_path / "project" / ".credentials.json"
        project.parent.mkdir(parents=True)
        project.write_text(json.dumps({"claudeAiOauth": {"token": "old"}}))
        os.utime(project, (0, 0))

        result = refresh_host_to_project(host, project)
        assert result is False

    def test_missing_oauth_key(self, tmp_path):
        host = tmp_path / "host" / ".credentials.json"
        host.parent.mkdir(parents=True)
        host.write_text(json.dumps({"someOther": "data"}))

        project = tmp_path / "project" / ".credentials.json"
        project.parent.mkdir(parents=True)
        project.write_text(json.dumps({"claudeAiOauth": {"token": "old"}}))
        os.utime(project, (0, 0))

        result = refresh_host_to_project(host, project)
        assert result is False

    def test_malformed_project_json(self, tmp_path):
        """When project JSON is malformed, it gets replaced with fresh data."""
        host = tmp_path / "host" / ".credentials.json"
        host.parent.mkdir(parents=True)
        host.write_text(json.dumps({"claudeAiOauth": {"token": "new"}}))

        project = tmp_path / "project" / ".credentials.json"
        project.parent.mkdir(parents=True)
        project.write_text("{bad json")
        os.utime(project, (0, 0))

        result = refresh_host_to_project(host, project)
        assert result is True
        data = json.loads(project.read_text())
        assert data["claudeAiOauth"]["token"] == "new"

    def test_empty_project_file(self, tmp_path):
        host = tmp_path / "host" / ".credentials.json"
        host.parent.mkdir(parents=True)
        host.write_text(json.dumps({"claudeAiOauth": {"token": "new"}}))

        project = tmp_path / "project" / ".credentials.json"
        project.parent.mkdir(parents=True)
        project.write_text("")
        os.utime(project, (0, 0))

        result = refresh_host_to_project(host, project)
        assert result is True
        data = json.loads(project.read_text())
        assert data["claudeAiOauth"]["token"] == "new"

    def test_project_missing_wholesale_copy(self, tmp_path):
        """When project creds don't exist, host is copied wholesale."""
        host = tmp_path / "host" / ".credentials.json"
        host.parent.mkdir(parents=True)
        host.write_text(json.dumps({"claudeAiOauth": {"token": "x"}, "extra": True}))

        project = tmp_path / "project" / ".credentials.json"
        # project doesn't exist
        result = refresh_host_to_project(host, project)
        assert result is True
        assert project.exists()
        data = json.loads(project.read_text())
        assert data["claudeAiOauth"]["token"] == "x"
        assert data["extra"] is True


# ---------------------------------------------------------------------------
# writeback_project_to_host
# ---------------------------------------------------------------------------

class TestWriteback:
    def test_writeback_to_host(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))

        project = tmp_path / "project" / ".credentials.json"
        project.parent.mkdir(parents=True)
        project.write_text(json.dumps({"claudeAiOauth": {"token": "fresh"}}))

        writeback_project_to_host(project)

        host_creds = home / ".claude" / ".credentials.json"
        assert host_creds.exists()
        host_data = json.loads(host_creds.read_text())
        assert host_data["claudeAiOauth"]["token"] == "fresh"

    def test_writeback_noop_when_no_project_creds(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))

        project = tmp_path / "project" / ".credentials.json"

        writeback_project_to_host(project)
        # No host creds created
        host_creds = home / ".claude" / ".credentials.json"
        assert not host_creds.exists()


# ---------------------------------------------------------------------------
# filter_settings edge cases
# ---------------------------------------------------------------------------

class TestFilterSettingsExtended:
    def test_bad_json_src(self, tmp_path):
        src = tmp_path / "src.json"
        src.write_text("{corrupt!")
        dst = tmp_path / "dst.json"
        filter_settings(src, dst)
        # dst should not be created when src is bad
        assert not dst.exists()

    def test_missing_keys(self, tmp_path):
        src = tmp_path / "src.json"
        src.write_text(json.dumps({"randomKey": 42}))
        dst = tmp_path / "dst.json"
        filter_settings(src, dst)
        data = json.loads(dst.read_text())
        # Only hasCompletedOnboarding (always True) should be present
        assert data == {"hasCompletedOnboarding": True}


class TestInvalidateCredentials:
    def test_removes_credential_files(self, tmp_path):
        shell = tmp_path / "shell"
        claude_dir = shell / ".claude"
        claude_dir.mkdir(parents=True)
        creds = claude_dir / ".credentials.json"
        creds.write_text('{"claudeAiOauth": {"token": "t"}}')
        settings = shell / ".claude.json"
        settings.write_text('{"oauthAccount": "a"}')

        ClaudeTarget().invalidate_credentials(shell)
        assert not creds.exists()
        assert not settings.exists()

    def test_noop_when_no_files(self, tmp_path):
        shell = tmp_path / "shell"
        shell.mkdir()
        # Should not raise
        ClaudeTarget().invalidate_credentials(shell)

    def test_partial_files(self, tmp_path):
        shell = tmp_path / "shell"
        shell.mkdir()
        settings = shell / ".claude.json"
        settings.write_text('{"oauthAccount": "a"}')

        ClaudeTarget().invalidate_credentials(shell)
        assert not settings.exists()

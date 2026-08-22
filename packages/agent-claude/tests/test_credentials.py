"""Tests for Claude credential functions (kanibako.plugins.claude.credentials)."""

from __future__ import annotations

import json
import os
import time

from kanibako.plugins.claude import ClaudeTarget
from kanibako.plugins.claude.credentials import (
    merge_oauth_account_out,
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


# NOTE: the host .claude.json allowlist filter (filter_settings) was removed in
# 1.6.0 along with the host-config import; its tests are deleted.


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


class TestMergeOauthAccountOut:
    """FIX 1: reverse-merge box ``.claude.json`` oauthAccount into the host's."""

    def test_creates_host_when_absent(self, tmp_path):
        box = tmp_path / "box" / ".claude.json"
        box.parent.mkdir(parents=True)
        box.write_text(json.dumps({"oauthAccount": {"emailAddress": "a@b.c"}}))
        host = tmp_path / "home" / ".claude.json"  # absent

        assert merge_oauth_account_out(box, host) is True
        assert host.exists()
        data = json.loads(host.read_text())
        assert data["oauthAccount"]["emailAddress"] == "a@b.c"

    def test_merges_without_clobbering_machine_fields(self, tmp_path):
        box = tmp_path / "box" / ".claude.json"
        box.parent.mkdir(parents=True)
        # Box also has machine-specific fields that MUST NOT reach the host.
        box.write_text(json.dumps({
            "oauthAccount": {"emailAddress": "new@b.c"},
            "machineID": "box-machine",
            "userID": "box-user",
            "projects": {"/box/path": {}},
        }))
        host = tmp_path / "home" / ".claude.json"
        host.parent.mkdir(parents=True)
        host.write_text(json.dumps({
            "oauthAccount": {"emailAddress": "old@b.c"},
            "machineID": "host-machine",
            "userID": "host-user",
            "projects": {"/host/path": {}},
            "otherHostKey": 123,
        }))

        assert merge_oauth_account_out(box, host) is True
        data = json.loads(host.read_text())
        # oauthAccount updated...
        assert data["oauthAccount"]["emailAddress"] == "new@b.c"
        # ...but host machine-specific fields untouched (NOT clobbered).
        assert data["machineID"] == "host-machine"
        assert data["userID"] == "host-user"
        assert data["projects"] == {"/host/path": {}}
        assert data["otherHostKey"] == 123

    def test_noop_when_box_file_absent(self, tmp_path):
        box = tmp_path / "box" / ".claude.json"  # absent
        host = tmp_path / "home" / ".claude.json"
        assert merge_oauth_account_out(box, host) is False
        assert not host.exists()

    def test_noop_when_no_oauth_account_key(self, tmp_path):
        box = tmp_path / "box" / ".claude.json"
        box.parent.mkdir(parents=True)
        box.write_text(json.dumps({"machineID": "x"}))
        host = tmp_path / "home" / ".claude.json"
        assert merge_oauth_account_out(box, host) is False
        assert not host.exists()

    def test_malformed_box_json_is_safe(self, tmp_path):
        box = tmp_path / "box" / ".claude.json"
        box.parent.mkdir(parents=True)
        box.write_text("{not json")
        host = tmp_path / "home" / ".claude.json"
        assert merge_oauth_account_out(box, host) is False

    def test_unreadable_host_not_clobbered(self, tmp_path):
        box = tmp_path / "box" / ".claude.json"
        box.parent.mkdir(parents=True)
        box.write_text(json.dumps({"oauthAccount": {"e": 1}}))
        host = tmp_path / "home" / ".claude.json"
        host.parent.mkdir(parents=True)
        host.write_text("{corrupt")
        # Bails rather than overwrite an unreadable host file.
        assert merge_oauth_account_out(box, host) is False
        assert host.read_text() == "{corrupt"


class TestWritebackExtra:
    """ClaudeTarget.writeback_extra wires merge_oauth_account_out."""

    def test_writeback_extra_merges_oauth_account(self, tmp_path):
        project_home = tmp_path / "project"
        host_home = tmp_path / "host"
        (project_home).mkdir()
        (host_home).mkdir()
        (project_home / ".claude.json").write_text(
            json.dumps({"oauthAccount": {"emailAddress": "z@z.z"}})
        )
        ClaudeTarget().writeback_extra(
            project_home=project_home, host_home=host_home,
        )
        host = host_home / ".claude.json"
        assert host.exists()
        assert json.loads(host.read_text())["oauthAccount"]["emailAddress"] == "z@z.z"


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

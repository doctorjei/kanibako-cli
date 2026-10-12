"""Tests for Claude credential functions (kanibako.plugins.claude.credentials)."""

from __future__ import annotations

import json
import logging

import pytest

from kanibako.plugins.claude import ClaudeTarget
from kanibako.plugins.claude.credentials import (
    merge_oauth_account_out,
    merge_oauth_in,
)


class TestMergeOauthIn:
    def test_creates_project_from_host(self, tmp_path):
        host = tmp_path / "host" / ".credentials.json"
        host.parent.mkdir()
        host.write_text(json.dumps({"claudeAiOauth": {"token": "abc"}}))

        project = tmp_path / "project" / ".credentials.json"
        project.parent.mkdir()

        assert merge_oauth_in(host, project)
        data = json.loads(project.read_text())
        assert data["claudeAiOauth"]["token"] == "abc"

    def test_merges_oauth_key(self, tmp_path):
        host = tmp_path / "host" / ".credentials.json"
        host.parent.mkdir()
        host.write_text(json.dumps({"claudeAiOauth": {"token": "new"}}))

        project = tmp_path / "project" / ".credentials.json"
        project.parent.mkdir()
        project.write_text(json.dumps({"claudeAiOauth": {"token": "old"}, "otherKey": True}))

        assert merge_oauth_in(host, project)
        data = json.loads(project.read_text())
        assert data["claudeAiOauth"]["token"] == "new"
        assert data["otherKey"] is True


# NOTE: the host .claude.json allowlist filter (filter_settings) was removed in
# 1.6.0 along with the host-config import; its tests are deleted.


# ---------------------------------------------------------------------------
# merge_oauth_in — error paths
# ---------------------------------------------------------------------------

class TestMergeOauthInErrors:
    def test_malformed_host_json(self, tmp_path):
        host = tmp_path / "host" / ".credentials.json"
        host.parent.mkdir(parents=True)
        host.write_text("{bad json!!")

        project = tmp_path / "project" / ".credentials.json"
        project.parent.mkdir(parents=True)
        project.write_text(json.dumps({"claudeAiOauth": {"token": "old"}}))

        result = merge_oauth_in(host, project)
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

        result = merge_oauth_in(host, project)
        assert result is False

    def test_missing_oauth_key(self, tmp_path):
        host = tmp_path / "host" / ".credentials.json"
        host.parent.mkdir(parents=True)
        host.write_text(json.dumps({"someOther": "data"}))

        project = tmp_path / "project" / ".credentials.json"
        project.parent.mkdir(parents=True)
        project.write_text(json.dumps({"claudeAiOauth": {"token": "old"}}))

        result = merge_oauth_in(host, project)
        assert result is False

    def test_malformed_project_json(self, tmp_path):
        """When project JSON is malformed, it gets replaced with fresh data."""
        host = tmp_path / "host" / ".credentials.json"
        host.parent.mkdir(parents=True)
        host.write_text(json.dumps({"claudeAiOauth": {"token": "new"}}))

        project = tmp_path / "project" / ".credentials.json"
        project.parent.mkdir(parents=True)
        project.write_text("{bad json")

        result = merge_oauth_in(host, project)
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

        result = merge_oauth_in(host, project)
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
        result = merge_oauth_in(host, project)
        assert result is True
        assert project.exists()
        data = json.loads(project.read_text())
        assert data["claudeAiOauth"]["token"] == "x"
        assert data["extra"] is True


class TestMergeOauthAccountOut:
    """FIX 1: reverse-merge box ``.claude.json`` oauthAccount into the host's."""

    def test_creates_host_when_absent(self, tmp_path):
        box = tmp_path / "box" / ".claude.json"
        box.parent.mkdir(parents=True)
        box.write_text(json.dumps({"oauthAccount": {"emailAddress": "a@b.c"}}))
        host = tmp_path / "home" / ".claude.json"  # absent

        assert merge_oauth_account_out(box, host, box_root=box.parent) is True
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

        assert merge_oauth_account_out(box, host, box_root=box.parent) is True
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
        assert merge_oauth_account_out(box, host, box_root=box.parent) is False
        assert not host.exists()

    def test_noop_when_no_oauth_account_key(self, tmp_path):
        box = tmp_path / "box" / ".claude.json"
        box.parent.mkdir(parents=True)
        box.write_text(json.dumps({"machineID": "x"}))
        host = tmp_path / "home" / ".claude.json"
        assert merge_oauth_account_out(box, host, box_root=box.parent) is False
        assert not host.exists()

    def test_malformed_box_json_is_safe(self, tmp_path):
        box = tmp_path / "box" / ".claude.json"
        box.parent.mkdir(parents=True)
        box.write_text("{not json")
        host = tmp_path / "home" / ".claude.json"
        assert merge_oauth_account_out(box, host, box_root=box.parent) is False

    def test_unreadable_host_not_clobbered(self, tmp_path):
        box = tmp_path / "box" / ".claude.json"
        box.parent.mkdir(parents=True)
        box.write_text(json.dumps({"oauthAccount": {"e": 1}}))
        host = tmp_path / "home" / ".claude.json"
        host.parent.mkdir(parents=True)
        host.write_text("{corrupt")
        # Bails rather than overwrite an unreadable host file.
        assert merge_oauth_account_out(box, host, box_root=box.parent) is False
        assert host.read_text() == "{corrupt"

    @pytest.mark.parametrize("to", ["outside", "inside"])
    def test_box_file_link_is_never_followed(self, tmp_path, to, caplog):
        """No-follow patch (B2): a box ``.claude.json`` that is a symlink -- to a host
        file outside the box or a file inside it -- is not read; the host file is
        untouched, the linked file too, and the warning names the box path."""
        root = tmp_path / "box"
        root.mkdir()
        held = tmp_path / "outside.json" if to == "outside" else root / "elsewhere.json"
        held.write_text(json.dumps({"oauthAccount": {"emailAddress": "leak@x"}}))
        before = held.read_bytes()
        box = root / ".claude.json"
        box.symlink_to(held)
        host = tmp_path / "home" / ".claude.json"
        with caplog.at_level(logging.WARNING):
            assert merge_oauth_account_out(box, host, box_root=root) is False
        assert not host.exists()
        assert held.read_bytes() == before
        assert str(box) in caplog.text and "symlink" in caplog.text


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

    def test_writeback_extra_skips_a_linked_box_file(self, tmp_path):
        project_home = tmp_path / "project"
        host_home = tmp_path / "host"
        project_home.mkdir()
        host_home.mkdir()
        secret = tmp_path / "secret.json"
        secret.write_text(json.dumps({"oauthAccount": {"emailAddress": "leak@x"}}))
        (project_home / ".claude.json").symlink_to(secret)
        ClaudeTarget().writeback_extra(project_home=project_home, host_home=host_home)
        assert not (host_home / ".claude.json").exists()
